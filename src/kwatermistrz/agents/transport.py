"""Agent Transportu – S22IT1S0P2-27 Wybór wariantu podróży na podstawie porównania tras.

Przebieg:
1. `zadanie.transport` (od Koordynatora po zatwierdzeniu) → `przygotuj_warianty`:
   - wybrać z tabeli `Polaczenie` połączenia z miejscowości jednostki do miejsca docelowego,
   - porównać czas, koszt, liczbę przesiadek, punktualność, bezpieczeństwo i zgodność z godziną
     rozpoczęcia przedsięwzięcia (`Przedsiewziecie.godzina_rozpoczecia`),
   - zapisać warianty jako `Przejazd` ze statusem `StatusRezerwacji.PROPOZYCJA`, jeden z `rekomendowany=True`
     i `uzasadnienie`,
   - zapisać decyzję: `ktx.decyzja(Decyzja(typ="Rekomendacja wariantu podróży", rodzaj="Probabilistyczna"
     albo "Regułowa", liczba_wariantow=..., pewnosc=...))`.
2. Aktor wybiera wariant przez API (`api/organizacja.py`) → `wybor.transport` → `zapisz_wybor`:
   - wybrany wariant: status `WYBRANA`, przyczyna wyboru innego niż rekomendowany w `uzasadnienie`,
   - odrzucenie wszystkich (6a): status `DO_UZGODNIENIA`,
   - `ktx.wyslij(Typ.WYNIK_TRANSPORTU)` – Koordynator sprawdzi, czy wyjazd jest zorganizowany.
"""

from datetime import datetime, timedelta

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..audit import Decyzja
from ..messaging import Komunikat, Typ
from ..models import (
    Delegacja,
    Jednostka,
    LogDecyzji,
    LogOperacji,
    Polaczenie,
    Przejazd,
    Status,
    StatusRezerwacji,
    Zolnierz,
)
from ..podroze import (
    BladPodrozy,
    WariantPodrozyIn,
    WyborPodrozy,
    nazwa_miejscowosci,
    ocena,
    przejazdy,
    sprawdz_decyzje,
    sprawdz_dodanie_wariantu,
    termin,
    zestawienie,
)
from .base import Agent, BladProcesu, Kontekst, pobierz_delegacje


def zablokuj_delegacje(s: Session, id_delegacji: str | None) -> Delegacja:
    d = s.scalar(select(Delegacja).where(Delegacja.id_delegacji == id_delegacji).with_for_update())
    if d is None:
        return pobierz_delegacje(s, id_delegacji)
    return d


class AgentTransportu(Agent):
    id_agenta = "AG-04"
    nazwa = "transport"
    etap = "Transport"
    wersja_modelu = "trasy-rules-1.0"

    def obslugi(self):
        return {
            Typ.ZADANIE_TRANSPORTU: (self.przygotuj_warianty, "Przygotowanie wariantów podróży"),
            Typ.WYBOR_TRANSPORTU: (self.zapisz_wybor, "Zapis wybranego wariantu podróży"),
            Typ.PROPOZYCJA_TRANSPORTU: (self.dodaj_wariant, "Dodanie sprawdzonej oferty przejazdu"),
        }

    def przygotuj_warianty(self, s: Session, k: Komunikat, ktx: Kontekst) -> None:
        d = zablokuj_delegacje(s, k.id_delegacji)
        if d.status != Status.ZATWIERDZONY:
            raise BladProcesu("S22IT1S0P2-27: delegacja nie jest zatwierdzona")
        if przejazdy(s, d):
            ktx.opis = "Warianty już przygotowano — pominięto ponowienie"
            return
        if not d.data_wyjazdu or not d.data_powrotu or not d.miejsce_docelowe:
            ktx.ostrzezenie("S22IT1S0P2-27: uzupełnij daty i miejsce podróży")
            return
        zolnierz = s.get(Zolnierz, d.id_zolnierza)
        jednostka = s.get(Jednostka, zolnierz.id_jednostki) if zolnierz else None
        if jednostka is None:
            raise BladProcesu("Brak jednostki wnioskodawcy")
        deadline = termin(s, d)
        for kierunek, skad, dokad, data in (
            ("Tam", jednostka.miejscowosc, d.miejsce_docelowe, d.data_wyjazdu),
            ("Powrót", d.miejsce_docelowe, jednostka.miejscowosc, d.data_powrotu),
        ):
            oferty = [
                p
                for p in s.scalars(select(Polaczenie))
                if not p.id_polaczenia.startswith("M-")
                and nazwa_miejscowosci(p.skad) == nazwa_miejscowosci(skad)
                and nazwa_miejscowosci(p.dokad) == nazwa_miejscowosci(dokad)
            ]
            oferty = [
                p
                for p in oferty
                if p.czas_min > 0
                and p.koszt >= 0
                and 0 <= p.punktualnosc <= 1
                and 1 <= p.ocena_bezpieczenstwa <= 5
                and p.liczba_przesiadek >= 0
            ]
            limit = deadline if kierunek == "Tam" else None
            oferty.sort(key=lambda p: ocena(p, data, limit))
            najlepsze = next((p for p in oferty if not ocena(p, data, limit)[0]), None)
            for p in oferty:
                rekomendowany = p is najlepsze
                powod = (
                    "Rekomendacja regułowa: termin, koszt, bezpieczeństwo, przesiadki, punktualność, czas."
                    if rekomendowany
                    else "Wariant alternatywny do porównania."
                )
                if ocena(p, data, limit)[0]:
                    powod = "Przyjazd po rozpoczęciu szkolenia — wariant niedostępny do wyboru."
                s.add(
                    Przejazd(
                        id_delegacji=d.id_delegacji,
                        kierunek=kierunek,
                        srodek_transportu=p.srodek_transportu,
                        data_przejazdu=data,
                        id_polaczenia=p.id_polaczenia,
                        koszt=p.koszt,
                        koszt_indywidualny=p.koszt,
                        status=StatusRezerwacji.PROPOZYCJA,
                        rekomendowany=rekomendowany,
                        uzasadnienie=powod,
                    )
                )
            ktx.decyzja(
                Decyzja(
                    typ="Rekomendacja wariantu podróży",
                    wybrany_wariant=f"{kierunek}: {najlepsze.id_polaczenia}"
                    if najlepsze
                    else f"{kierunek}: do uzgodnienia",
                    liczba_wariantow=len(oferty),
                    uzasadnienie="Terminowość, następnie koszt, bezpieczeństwo, przesiadki, punktualność i czas.",
                    decyzja_finansowa=True,
                )
            )
            if najlepsze is None:
                ktx.ostrzezenie(f"Brak odpowiedniego wariantu: {kierunek}; konieczne uzgodnienie")
        ktx.wyslij(
            Typ.POWIADOMIENIE,
            {
                "do": d.id_zatwierdzajacego or d.id_zolnierza,
                "tresc": f"Porównaj warianty podróży delegacji {d.id_delegacji}",
            },
        )

    def zapisz_wybor(self, s: Session, k: Komunikat, ktx: Kontekst) -> None:
        d = zablokuj_delegacje(s, k.id_delegacji)
        if not d.id_zatwierdzajacego or k.dane.get("id_osoby") != d.id_zatwierdzajacego:
            raise BladProcesu("Wyboru może dokonać tylko osoba akceptująca podróż")
        try:
            wybor = WyborPodrozy.model_validate({key: value for key, value in k.dane.items() if key != "id_osoby"})
            rows = przejazdy(s, d)
            wybrane_ids = {r.id_przejazdu for r in rows if r.status == StatusRezerwacji.WYBRANA}
            ponowienie = (not wybor.odrzuc_wszystkie and wybrane_ids == {wybor.id_tam, wybor.id_powrot}) or (
                wybor.odrzuc_wszystkie and rows and all(r.status == StatusRezerwacji.DO_UZGODNIENIA for r in rows)
            )
            if ponowienie:
                ktx.opis = "Ta decyzja została już zapisana — pominięto ponowienie"
                ktx.wyslij(Typ.WYNIK_TRANSPORTU)
                return
            wybrane = sprawdz_decyzje(s, d, wybor)
        except (ValidationError, BladPodrozy) as e:
            raise BladProcesu(str(e)) from e
        rekomendowane = {
            w["id_przejazdu"] for w in zestawienie(s, d, wybor.kryterium)["warianty"] if w["rekomendowany"]
        }
        for r in rows:
            r.rekomendowany = r.id_przejazdu in rekomendowane
            if wybor.odrzuc_wszystkie or r in wybrane:
                r.status = StatusRezerwacji.DO_UZGODNIENIA if wybor.odrzuc_wszystkie else StatusRezerwacji.WYBRANA
                r.uzasadnienie = wybor.uzasadnienie or "Zaakceptowano rekomendację"
        zaakceptowane = {f"{r.kierunek}: {r.id_polaczenia}" for r in wybrane}
        rekomendacje = s.scalars(
            select(LogDecyzji)
            .join(LogOperacji)
            .where(
                LogOperacji.id_delegacji == d.id_delegacji,
                LogOperacji.id_agenta == self.id_agenta,
                LogDecyzji.typ_decyzji.in_(("Rekomendacja wariantu podróży", "Dodanie wariantu podróży")),
            )
        )
        for wpis in rekomendacje:
            wpis.decyzja_czlowieka = "Zatwierdzona" if wpis.wybrany_wariant in zaakceptowane else "Odrzucona"
            wpis.id_weryfikujacego = k.dane["id_osoby"]
        if wybor.odrzuc_wszystkie:
            ktx.ostrzezenie("Odrzucono wszystkie warianty; podróż wymaga dodatkowego uzgodnienia")
            ktx.wyslij(
                Typ.POWIADOMIENIE,
                {
                    "do": d.id_zatwierdzajacego,
                    "tresc": "Podróż wymaga dodatkowego uzgodnienia",
                },
            )
        ktx.decyzja(
            Decyzja(
                typ="Zapis wyboru podróży",
                liczba_wariantow=len(rows),
                wybrany_wariant="Do uzgodnienia"
                if wybor.odrzuc_wszystkie
                else f"Tam: {wybor.id_tam}; Powrót: {wybor.id_powrot}",
                uzasadnienie=f"Osoba: {k.dane['id_osoby']}; kryterium: {wybor.kryterium}; "
                + (wybor.uzasadnienie or "Zaakceptowano rekomendację"),
            )
        )
        ktx.wyslij(Typ.WYNIK_TRANSPORTU)

    def dodaj_wariant(self, s: Session, k: Komunikat, ktx: Kontekst) -> None:
        d = zablokuj_delegacje(s, k.id_delegacji)
        if not d.id_zatwierdzajacego or k.dane.get("id_osoby") != d.id_zatwierdzajacego:
            raise BladProcesu("Ofertę może dodać osoba akceptująca podróż")
        try:
            w = WariantPodrozyIn.model_validate({p: v for p, v in k.dane.items() if p != "id_osoby"})
            istniejacy = s.scalar(select(Przejazd).where(Przejazd.id_polaczenia == w.id_polaczenia))
            if istniejacy:
                p = s.get(Polaczenie, w.id_polaczenia)
                if (
                    istniejacy.id_delegacji != d.id_delegacji
                    or istniejacy.kierunek != w.kierunek
                    or p.przewoznik != w.przewoznik
                    or p.srodek_transportu != w.srodek_transportu
                    or p.godzina_odjazdu != w.godzina_odjazdu
                    or p.czas_min != w.czas_min
                    or p.koszt != w.koszt
                    or p.liczba_przesiadek != w.liczba_przesiadek
                    or p.punktualnosc != float(w.punktualnosc / 100)
                    or p.ocena_bezpieczenstwa != w.ocena_bezpieczenstwa
                    or istniejacy.powod_zmiany != f"Źródło: {w.zrodlo}\nDodał: {k.dane['id_osoby']}"
                ):
                    raise BladPodrozy("Klucz oferty został już wykorzystany dla innych danych")
                ktx.opis = "Oferta już zapisana — pominięto ponowienie"
                return
            sprawdz_dodanie_wariantu(s, d)
        except (ValidationError, BladPodrozy) as e:
            raise BladProcesu(str(e)) from e
        zolnierz = s.get(Zolnierz, d.id_zolnierza)
        jednostka = s.get(Jednostka, zolnierz.id_jednostki)
        skad, dokad = jednostka.miejscowosc, d.miejsce_docelowe
        data = d.data_wyjazdu
        if w.kierunek == "Powrót":
            skad, dokad, data = dokad, skad, d.data_powrotu
        przyjazd = datetime.combine(data, w.godzina_odjazdu) + timedelta(minutes=w.czas_min)
        s.add(
            Polaczenie(
                id_polaczenia=w.id_polaczenia,
                skad=skad,
                dokad=dokad,
                srodek_transportu=w.srodek_transportu,
                przewoznik=w.przewoznik,
                godzina_odjazdu=w.godzina_odjazdu,
                godzina_przyjazdu=przyjazd.time(),
                czas_min=w.czas_min,
                koszt=w.koszt,
                liczba_przesiadek=w.liczba_przesiadek,
                punktualnosc=float(w.punktualnosc / 100),
                ocena_bezpieczenstwa=w.ocena_bezpieczenstwa,
            )
        )
        s.flush()
        s.add(
            Przejazd(
                id_delegacji=d.id_delegacji,
                kierunek=w.kierunek,
                data_przejazdu=data,
                id_polaczenia=w.id_polaczenia,
                srodek_transportu=w.srodek_transportu,
                koszt=w.koszt,
                koszt_indywidualny=w.koszt,
                status=StatusRezerwacji.PROPOZYCJA,
                uzasadnienie="Oferta wprowadzona przez osobę akceptującą do porównania",
                powod_zmiany=f"Źródło: {w.zrodlo}\nDodał: {k.dane['id_osoby']}",
            )
        )
        s.flush()
        rekomendowane = {w["id_przejazdu"] for w in zestawienie(s, d)["warianty"] if w["rekomendowany"]}
        for r in przejazdy(s, d):
            r.rekomendowany = r.id_przejazdu in rekomendowane
        ktx.decyzja(
            Decyzja(
                typ="Dodanie wariantu podróży",
                wybrany_wariant=f"{w.kierunek}: {w.id_polaczenia}",
                uzasadnienie=f"Źródło: {w.zrodlo}; wprowadził: {k.dane['id_osoby']}",
                decyzja_finansowa=True,
            )
        )
