"""Agent Zakwaterowania – S22IT1S0P2-39 Dobór zakwaterowania zapewniającego terminowy dojazd na miejsce zadania.

Przebieg:
1. `zadanie.zakwaterowanie` (od Koordynatora, gdy `Delegacja.potrzeba_zakwaterowania`) → `przygotuj_propozycje`:
   - obiekty z `ObiektZakwaterowania` w miejscowości przedsięwzięcia z wolnymi miejscami, cena ≤
     `stawki.LIMIT_NOCLEGOWY` i czas dojazdu pozwalający zdążyć na `Przedsiewziecie.godzina_rozpoczecia`
     (`terminowy_dojazd`); obiekty wojskowe mają pierwszeństwo, potem tańsze,
   - propozycje zapisane jako `Zakwaterowanie` ze statusem `StatusRezerwacji.PROPOZYCJA` (koszt, czas dojazdu),
   - brak obiektu w limicie z terminowym dojazdem (2a): warianty z terminowym dojazdem ponad limitem,
     z `wymaga_odstepstwa=True` – wybór takiego wariantu trafia do przełożonego,
   - decyzja w logu (`TYP_DECYZJI`).
2. Delegowany wybiera obiekt (`/app/wnioski/{id}/zakwaterowanie` albo `api/organizacja.py`) → `wybor.zakwaterowanie`
   → `zapisz_wybor` (S22IT1S0P2-99, `agents/rezerwacja.py`).
3. S22IT1S0P2-40: wybór obiektu z katalogu z kontrolą limitu i decyzja przełożonego o odstępstwie
   (`agents/limit_noclegu.py`).
4. S22IT1S0P2-15: brak miejsc w obiektach wojskowych – informacja i obiekty cywilne w limicie, uzasadnienie
   i dokumentacja noclegu, odrzucenie wszystkich obiektów (`agents/zakwaterowanie_alternatywne.py`).
"""

from datetime import datetime, time, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..audit import Decyzja
from ..messaging import Komunikat, Typ
from ..models import Delegacja, ObiektZakwaterowania, Przedsiewziecie, StatusRezerwacji, Zakwaterowanie
from ..stawki import LIMIT_NOCLEGOWY
from .base import Agent, Kontekst, pobierz_delegacje

TYP_DECYZJI = "Dobór zakwaterowania"
# Terminowy dojazd: wyjście z obiektu nie wcześniej niż o 7:00 i przyjazd z 15-minutowym zapasem przed zajęciami
NAJWCZESNIEJSZE_WYJSCIE = time(7, 0)
ZAPAS_MIN = 15
DOMYSLNA_GODZINA_ROZPOCZECIA = time(8, 0)  # przedsięwzięcie bez podanej godziny


def maks_czas_dojazdu(godzina_rozpoczecia: time | None) -> int:
    """Najdłuższy czas dojazdu (min), przy którym delegowany zdąży na rozpoczęcie zajęć."""
    start = godzina_rozpoczecia or DOMYSLNA_GODZINA_ROZPOCZECIA
    dzien = datetime(2000, 1, 1)
    dostepne = datetime.combine(dzien, start) - datetime.combine(dzien, NAJWCZESNIEJSZE_WYJSCIE)
    return max(0, int(dostepne / timedelta(minutes=1)) - ZAPAS_MIN)


def terminowy_dojazd(obiekt: ObiektZakwaterowania, maks_min: int) -> bool:
    return obiekt.czas_dojazdu_min is not None and obiekt.czas_dojazdu_min <= maks_min


def kolejnosc(obiekt: ObiektZakwaterowania) -> tuple:
    """Obiekty wojskowe pierwsze, potem tańsze, potem bliższe."""
    return (obiekt.rodzaj != "Wojskowy", obiekt.cena_za_dobe, obiekt.czas_dojazdu_min or 0)


class AgentZakwaterowania(Agent):
    id_agenta = "AG-05"
    nazwa = "zakwaterowanie"
    etap = "Zakwaterowanie"
    wersja_modelu = "nocleg-0.1"

    def obslugi(self):
        return {
            Typ.ZADANIE_ZAKWATEROWANIA: (self.przygotuj_propozycje, "Dobór obiektów zakwaterowania"),
            Typ.WYBOR_ZAKWATEROWANIA: (self.zapisz_wybor, "Zapis wybranego zakwaterowania"),
            # S22IT1S0P2-40
            Typ.WYBOR_OBIEKTU: (self.wybierz_obiekt, "Wybór obiektu z kontrolą limitu noclegowego"),
            Typ.DECYZJA_ODSTEPSTWA: (self.decyduj_o_odstepstwie, "Decyzja o odstępstwie od limitu noclegowego"),
            # S22IT1S0P2-15
            Typ.ODRZUCENIE_ZAKWATEROWANIA: (self.odrzuc_propozycje, "Odrzucenie propozycji zakwaterowania"),
        }

    def przygotuj_propozycje(self, s: Session, k: Komunikat, ktx: Kontekst) -> None:
        d = pobierz_delegacje(s, k.id_delegacji)

        # Ponowione zlecenie nie dubluje propozycji
        if s.scalar(select(Zakwaterowanie.id_zakwaterowania).where(Zakwaterowanie.id_delegacji == d.id_delegacji)):
            ktx.opis = "Propozycje zakwaterowania zostały już przygotowane"
            return
        if d.data_wyjazdu is None or d.data_powrotu is None:
            ktx.ostrzezenie("Brak dat wyjazdu i powrotu – nie można dobrać zakwaterowania")
            return

        przedsiewziecie = s.get(Przedsiewziecie, d.id_przedsiewziecia) if d.id_przedsiewziecia else None
        miejscowosc = przedsiewziecie.miejscowosc if przedsiewziecie else d.miejsce_docelowe
        maks_min = maks_czas_dojazdu(przedsiewziecie.godzina_rozpoczecia if przedsiewziecie else None)
        dostepne = s.scalars(
            select(ObiektZakwaterowania).where(
                ObiektZakwaterowania.miejscowosc == miejscowosc, ObiektZakwaterowania.liczba_wolnych_miejsc > 0
            )
        ).all()
        terminowe = sorted((o for o in dostepne if terminowy_dojazd(o, maks_min)), key=kolejnosc)
        w_limicie = [o for o in terminowe if o.cena_za_dobe <= LIMIT_NOCLEGOWY]
        warunki = f"{miejscowosc}, wolne miejsca, dojazd ≤ {maks_min} min"

        if w_limicie:
            self._zapisz(s, d, w_limicie, wymaga_odstepstwa=False)
            ktx.opis = f"Przygotowano {len(w_limicie)} propozycji zakwaterowania w limicie ({warunki})"
            if all(o.rodzaj != "Wojskowy" for o in w_limicie):  # S22IT1S0P2-15 (130): same obiekty cywilne
                from .zakwaterowanie_alternatywne import komunikat_brak_wojskowych

                ktx.opis += "; " + komunikat_brak_wojskowych(miejscowosc).lower()
                tresc = f"Delegacja {d.id_delegacji}: {komunikat_brak_wojskowych(miejscowosc)}"
                ktx.wyslij(Typ.POWIADOMIENIE, {"do": d.id_zolnierza, "tresc": tresc})
            ktx.decyzja(
                Decyzja(
                    typ=TYP_DECYZJI,
                    wybrany_wariant=w_limicie[0].nazwa,
                    uzasadnienie=f"Obiekty w limicie {LIMIT_NOCLEGOWY} zł za dobę ({warunki}); "
                    "pierwszeństwo obiektów wojskowych, potem niższa cena",
                    liczba_wariantow=len(w_limicie),
                )
            )
            return

        # Scenariusz 2a: nic w limicie – warianty ponad limitem, ale z terminowym dojazdem
        if terminowe:
            self._zapisz(s, d, terminowe, wymaga_odstepstwa=True)
            opis = (
                f"Brak obiektu w limicie {LIMIT_NOCLEGOWY} zł z terminowym dojazdem – "
                f"{len(terminowe)} wariantów wymaga odstępstwa ({warunki})"
            )
            ktx.ostrzezenie(opis)
            ktx.decyzja(
                Decyzja(
                    typ=TYP_DECYZJI,
                    wybrany_wariant=terminowe[0].nazwa,
                    uzasadnienie=opis,
                    liczba_wariantow=len(terminowe),
                    decyzja_finansowa=True,
                )
            )
            ktx.wyslij(
                Typ.POWIADOMIENIE,
                {"do": d.id_zolnierza, "tresc": f"Delegacja {d.id_delegacji}: zakwaterowanie wymaga odstępstwa"},
            )
            return

        opis = f"Brak obiektu zakwaterowania z terminowym dojazdem ({warunki})"
        ktx.ostrzezenie(opis)
        ktx.decyzja(Decyzja(typ=TYP_DECYZJI, wybrany_wariant="Brak propozycji", uzasadnienie=opis, liczba_wariantow=0))
        ktx.wyslij(Typ.POWIADOMIENIE, {"do": d.id_zatwierdzajacego or d.id_zolnierza, "tresc": opis})

    @staticmethod
    def _zapisz(s: Session, d: Delegacja, obiekty: list[ObiektZakwaterowania], wymaga_odstepstwa: bool) -> None:
        noce = max(1, (d.data_powrotu - d.data_wyjazdu).days)
        for o in obiekty:
            s.add(
                Zakwaterowanie(
                    id_delegacji=d.id_delegacji,
                    id_obiektu=o.id_obiektu,
                    data_od=d.data_wyjazdu,
                    data_do=d.data_powrotu,
                    cena_za_dobe=o.cena_za_dobe,
                    koszt_calkowity=o.cena_za_dobe * noce,
                    status=StatusRezerwacji.PROPOZYCJA,
                    czas_dojazdu_min=o.czas_dojazdu_min,
                    wymaga_odstepstwa=wymaga_odstepstwa,
                )
            )

    def zapisz_wybor(self, s: Session, k: Komunikat, ktx: Kontekst) -> None:
        # S22IT1S0P2-99: rezerwacja wybranego obiektu – logika w agents/rezerwacja.py
        from .rezerwacja import zapisz_wybor_zakwaterowania

        zapisz_wybor_zakwaterowania(s, k, ktx)

    def wybierz_obiekt(self, s: Session, k: Komunikat, ktx: Kontekst) -> None:
        from .limit_noclegu import wybierz_obiekt  # S22IT1S0P2-40 (import lokalny – moduł korzysta z reguł tego)

        wybierz_obiekt(s, k, ktx)

    def decyduj_o_odstepstwie(self, s: Session, k: Komunikat, ktx: Kontekst) -> None:
        from .limit_noclegu import decyduj_o_odstepstwie

        decyduj_o_odstepstwie(s, k, ktx)

    def odrzuc_propozycje(self, s: Session, k: Komunikat, ktx: Kontekst) -> None:
        from .zakwaterowanie_alternatywne import odrzuc_propozycje  # S22IT1S0P2-15 (3a–4a)

        odrzuc_propozycje(s, k, ktx)
