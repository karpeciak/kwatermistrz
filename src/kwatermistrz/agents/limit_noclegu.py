"""Zastąpienie zakwaterowania przekraczającego limit wariantem zgodnym z limitem – S22IT1S0P2-40 (podzadania 153–156).

Obsługę wywołuje Agent Zakwaterowania (AG-05):
1. Delegowany wybiera obiekt z katalogu (`/app/wnioski/{id}/zakwaterowanie`, „Inny obiekt z katalogu”)
   → `wybor.obiekt_zakwaterowania` → `wybierz_obiekt` (podzadanie 153):
   - obiekt w limicie (`stawki.LIMIT_NOCLEGOWY`) z wolnym miejscem i terminowym dojazdem → rezerwacja (`zarezerwuj`),
   - obiekt ponad limitem → rezerwacja wstrzymana (`WSTRZYMANA`), informacja o przekroczeniu i propozycje w limicie
     (Agent Zakwaterowania, S22IT1S0P2-39 – obiekty wojskowe pierwsze); brak propozycji w limicie (2a) → można
     złożyć wniosek o odstępstwo dla pierwotnie wybranego obiektu.
2. Wniosek o odstępstwo z uzasadnieniem (3a) – `agents/rezerwacja.py` → status `DO_DECYZJI_PRZELOZONEGO`.
3. Przełożony decyduje (`/app/wnioski/{id}/odstepstwo`) → `decyzja.odstepstwo_noclegu` → `decyduj_o_odstepstwie`
   (podzadanie 155): zgoda → rezerwacja pierwotnie wybranego obiektu i informacja dla delegowanego (4a),
   odmowa → wniosek odrzucony, delegowany wybiera obiekt w limicie.
4. `zarezerwuj` (krok 4): zgodność z limitem albo zatwierdzone odstępstwo, zgodność z budżetem jednostki,
   rezerwacja miejsca i aktualizacja zobowiązań budżetu: przy zatwierdzeniu wniosku zaangażowano szacunek
   noclegów w limicie (`stawki.szacuj_koszt`), więc budżet i szacowany koszt delegacji zmieniają się o różnicę
   między faktycznym kosztem noclegu a tym szacunkiem.
"""

from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..audit import Decyzja
from ..messaging import Komunikat, Typ
from ..models import (
    Delegacja,
    ObiektZakwaterowania,
    Przedsiewziecie,
    Status,
    StatusRezerwacji,
    Zakwaterowanie,
    Zolnierz,
)
from ..przynaleznosc import jednostka_delegacji
from ..stawki import LIMIT_NOCLEGOWY
from .base import BladProcesu, Kontekst, pobierz_delegacje
from .zakwaterowanie import kolejnosc, maks_czas_dojazdu, terminowy_dojazd
from .zakwaterowanie_alternatywne import uzasadnienie_wyboru, uzupelnij_dokumentacje
from .zmiana_przejazdu import dostepny_budzet

TYP_WSTRZYMANIA = "Kontrola limitu noclegowego"
TYP_ODSTEPSTWA = "Decyzja o odstępstwie od limitu noclegowego"
# Wybór albo decyzja w toku – kolejny wybór obiektu jest wtedy niemożliwy
ZAJETE = (StatusRezerwacji.WYBRANA, StatusRezerwacji.DO_DECYZJI_PRZELOZONEGO)


# ---------------------------------------------------------------- zapytania wspólne dla agenta, API i ekranu


def noclegi(s: Session, d: Delegacja, *statusy: str) -> list[Zakwaterowanie]:
    return list(
        s.scalars(
            select(Zakwaterowanie)
            .where(Zakwaterowanie.id_delegacji == d.id_delegacji, Zakwaterowanie.status.in_(statusy))
            .order_by(Zakwaterowanie.id_zakwaterowania)
        )
    )


def propozycje_w_limicie(s: Session, d: Delegacja) -> list[Zakwaterowanie]:
    """Propozycje mieszczące się w limicie – obiekty wojskowe pierwsze, potem tańsze i bliższe."""
    propozycje = [z for z in noclegi(s, d, StatusRezerwacji.PROPOZYCJA) if not z.wymaga_odstepstwa]
    obiekty = {o.id_obiektu: o for o in s.scalars(select(ObiektZakwaterowania))}
    return sorted(propozycje, key=lambda z: kolejnosc(obiekty[z.id_obiektu]) if z.id_obiektu in obiekty else (True,))


def wstrzymana_rezerwacja(s: Session, d: Delegacja) -> Zakwaterowanie | None:
    wstrzymane = noclegi(s, d, StatusRezerwacji.WSTRZYMANA)
    return wstrzymane[-1] if wstrzymane else None


def wniosek_o_odstepstwo(s: Session, d: Delegacja) -> Zakwaterowanie | None:
    wnioski = noclegi(s, d, StatusRezerwacji.DO_DECYZJI_PRZELOZONEGO)
    return wnioski[-1] if wnioski else None


def miejscowosc_zadania(s: Session, d: Delegacja) -> str | None:
    p = s.get(Przedsiewziecie, d.id_przedsiewziecia) if d.id_przedsiewziecia else None
    return p.miejscowosc if p else d.miejsce_docelowe


def katalog_obiektow(s: Session, d: Delegacja) -> list[ObiektZakwaterowania]:
    """Obiekty w miejscowości zadania, których nie ma wśród propozycji – do samodzielnego wyboru delegowanego."""
    zaproponowane = {z.id_obiektu for z in noclegi(s, d, StatusRezerwacji.PROPOZYCJA)}
    obiekty = s.scalars(
        select(ObiektZakwaterowania).where(ObiektZakwaterowania.miejscowosc == miejscowosc_zadania(s, d))
    ).all()
    return sorted((o for o in obiekty if o.id_obiektu not in zaproponowane), key=kolejnosc)


def przelozony(s: Session, d: Delegacja) -> str | None:
    wnioskodawca = s.get(Zolnierz, d.id_zolnierza)
    return d.id_zatwierdzajacego or (wnioskodawca.id_przelozonego if wnioskodawca else None)


def _kwota(x: Decimal) -> str:
    return f"{x:.2f} zł"


# ---------------------------------------------------------------- krok 4: rezerwacja i zobowiązania budżetu


def zmiana_zobowiazania(z: Zakwaterowanie) -> Decimal:
    """Faktyczny koszt noclegu minus szacunek w limicie, zaangażowany w budżecie przy zatwierdzeniu wniosku."""
    noce = max(1, (z.data_do - z.data_od).days)
    return z.koszt_calkowity - LIMIT_NOCLEGOWY * noce


def zarezerwuj(s: Session, d: Delegacja, z: Zakwaterowanie, ktx: Kontekst, uzasadnienie: str) -> bool:
    """Rezerwacja po sprawdzeniu limitu (albo zatwierdzonego odstępstwa), miejsc i budżetu jednostki."""
    obiekt = s.get(ObiektZakwaterowania, z.id_obiektu)
    nazwa = obiekt.nazwa if obiekt else z.id_obiektu
    if obiekt is not None and obiekt.liczba_wolnych_miejsc < 1:
        ktx.ostrzezenie(f"Brak wolnych miejsc w obiekcie {nazwa} – rezerwacja niemożliwa")
        return False
    roznica = zmiana_zobowiazania(z)
    budzet = dostepny_budzet(s, d)
    if roznica > 0 and budzet is not None and roznica > budzet:
        ktx.ostrzezenie(f"Nocleg {nazwa} przekracza dostępny budżet jednostki ({_kwota(budzet)}) – bez rezerwacji")
        return False

    if obiekt is not None:
        obiekt.liczba_wolnych_miejsc -= 1
    z.status = StatusRezerwacji.WYBRANA
    z.uzasadnienie = uzasadnienie_wyboru(s, d, obiekt, uzasadnienie)  # S22IT1S0P2-15: obiekt cywilny
    for wstrzymana in noclegi(s, d, StatusRezerwacji.WSTRZYMANA):  # wybrano obiekt w limicie zamiast wstrzymanego
        if wstrzymana is not z:
            wstrzymana.status = StatusRezerwacji.ANULOWANA
    jednostka = jednostka_delegacji(s, d)
    if jednostka is not None and roznica:
        jednostka.budzet_wykorzystany = max(Decimal("0"), (jednostka.budzet_wykorzystany or Decimal("0")) + roznica)
    if d.szacowany_koszt is not None and roznica:
        d.szacowany_koszt = max(Decimal("0"), d.szacowany_koszt + roznica)
    ktx.opis = f"Zarezerwowano {nazwa}, {z.data_od:%d.%m}–{z.data_do:%d.%m}, {_kwota(z.koszt_calkowity)}" + (
        f"; zobowiązanie budżetu {roznica:+.2f} zł" if roznica else ""
    )
    if uzupelnij_dokumentacje(s, d, z, obiekt):  # S22IT1S0P2-15: dane noclegu w poleceniu wyjazdu
        ktx.opis += "; polecenie wyjazdu uzupełnione o nocleg"
    ktx.wyslij(Typ.WYNIK_ZAKWATEROWANIA)
    return True


# ---------------------------------------------------------------- 1. wybór obiektu z katalogu (podzadanie 153)


def wybierz_obiekt(s: Session, k: Komunikat, ktx: Kontekst) -> None:
    d = pobierz_delegacje(s, k.id_delegacji)
    if k.dane.get("id_osoby") not in (None, d.id_zolnierza):
        raise BladProcesu("Obiekt zakwaterowania wybiera delegowany")
    if d.status != Status.ZATWIERDZONY or d.data_wyjazdu is None or d.data_powrotu is None:
        ktx.ostrzezenie("Zakwaterowanie wybiera się po zatwierdzeniu wniosku z datami wyjazdu i powrotu")
        return
    if noclegi(s, d, *ZAJETE):
        ktx.ostrzezenie("Zakwaterowanie dla tej delegacji zostało już wybrane – wybór pominięty")
        return
    obiekt = s.get(ObiektZakwaterowania, k.dane.get("id_obiektu"))
    if obiekt is None or obiekt.miejscowosc != miejscowosc_zadania(s, d):
        ktx.ostrzezenie("Obiekt nie znajduje się w miejscowości zadania – wybór pominięty")
        return
    p = s.get(Przedsiewziecie, d.id_przedsiewziecia) if d.id_przedsiewziecia else None
    maks_min = maks_czas_dojazdu(p.godzina_rozpoczecia if p else None)
    if not terminowy_dojazd(obiekt, maks_min):
        ktx.ostrzezenie(f"Z obiektu {obiekt.nazwa} nie da się dojechać na czas (≤ {maks_min} min) – wybór pominięty")
        return
    if obiekt.liczba_wolnych_miejsc < 1:
        ktx.ostrzezenie(f"Brak wolnych miejsc w obiekcie {obiekt.nazwa} – wybór pominięty")
        return

    noce = max(1, (d.data_powrotu - d.data_wyjazdu).days)
    for poprzednia in noclegi(s, d, StatusRezerwacji.WSTRZYMANA):  # nowy wybór zastępuje wstrzymany
        poprzednia.status = StatusRezerwacji.ANULOWANA
    z = Zakwaterowanie(
        id_delegacji=d.id_delegacji,
        id_obiektu=obiekt.id_obiektu,
        data_od=d.data_wyjazdu,
        data_do=d.data_powrotu,
        cena_za_dobe=obiekt.cena_za_dobe,
        koszt_calkowity=obiekt.cena_za_dobe * noce,
        czas_dojazdu_min=obiekt.czas_dojazdu_min,
        wymaga_odstepstwa=obiekt.cena_za_dobe > LIMIT_NOCLEGOWY,
        status=StatusRezerwacji.PROPOZYCJA,
    )
    s.add(z)
    s.flush()

    if not z.wymaga_odstepstwa:
        if zarezerwuj(s, d, z, ktx, "Obiekt wybrany z katalogu – w limicie noclegowym i budżecie jednostki"):
            ktx.decyzja(Decyzja(typ=TYP_WSTRZYMANIA, wybrany_wariant=obiekt.nazwa, uzasadnienie="Obiekt w limicie"))
        return

    # Krok 2: przekroczony limit – rezerwacja wstrzymana, propozycje w limicie albo wniosek o odstępstwo (2a)
    z.status = StatusRezerwacji.WSTRZYMANA
    nadwyzka = obiekt.cena_za_dobe - LIMIT_NOCLEGOWY
    z.uzasadnienie = (
        f"Cena {_kwota(obiekt.cena_za_dobe)} za dobę przekracza limit {_kwota(LIMIT_NOCLEGOWY)} o {_kwota(nadwyzka)}"
    )
    w_limicie = propozycje_w_limicie(s, d)
    if w_limicie:
        dalej = f"wybierz jeden z obiektów w limicie ({len(w_limicie)}), najpierw wojskowe"
    else:
        dalej = "brak obiektów w limicie – możesz złożyć wniosek o odstępstwo z uzasadnieniem"
    ktx.ostrzezenie(f"Wstrzymano rezerwację {obiekt.nazwa}: {z.uzasadnienie.lower()}; {dalej}")
    ktx.decyzja(
        Decyzja(
            typ=TYP_WSTRZYMANIA,
            wybrany_wariant="Wstrzymano rezerwację",
            uzasadnienie=f"{obiekt.nazwa}: {z.uzasadnienie}",
            liczba_wariantow=len(w_limicie),
            decyzja_finansowa=True,
        )
    )
    ktx.wyslij(
        Typ.POWIADOMIENIE,
        {
            "do": d.id_zolnierza,
            "tresc": f"Delegacja {d.id_delegacji}: obiekt {obiekt.nazwa} przekracza limit noclegowy – {dalej}",
        },
    )


# ---------------------------------------------------------------- 3. decyzja przełożonego (podzadanie 155)


def decyduj_o_odstepstwie(s: Session, k: Komunikat, ktx: Kontekst) -> None:
    d = pobierz_delegacje(s, k.id_delegacji)
    if k.dane.get("id_osoby") != przelozony(s, d):
        raise BladProcesu("O odstępstwie od limitu decyduje przełożony delegowanego")
    z = wniosek_o_odstepstwo(s, d)
    if z is None:
        ktx.opis = "Brak wniosku o odstępstwo oczekującego na decyzję – decyzja została już zapisana"
        return
    obiekt = s.get(ObiektZakwaterowania, z.id_obiektu)
    nazwa = obiekt.nazwa if obiekt else z.id_obiektu
    uwagi = str(k.dane.get("uzasadnienie") or "").strip()

    if not k.dane.get("zgoda"):
        if not uwagi:
            raise BladProcesu("Podaj przyczynę odmowy odstępstwa")
        z.status = StatusRezerwacji.ANULOWANA
        z.uzasadnienie = f"{z.uzasadnienie or ''}\nOdmowa przełożonego: {uwagi}".strip()
        ktx.ostrzezenie(f"Przełożony nie zgodził się na odstępstwo dla {nazwa}: {uwagi}")
        ktx.decyzja(Decyzja(typ=TYP_ODSTEPSTWA, wybrany_wariant="Odmowa", uzasadnienie=uwagi, decyzja_finansowa=True))
        ktx.wyslij(
            Typ.POWIADOMIENIE,
            {
                "do": d.id_zolnierza,
                "tresc": f"Delegacja {d.id_delegacji}: przełożony nie zgodził się na odstępstwo od limitu dla {nazwa} "
                f"({uwagi}). Wybierz obiekt mieszczący się w limicie.",
            },
        )
        return

    uzasadnienie = f"Odstępstwo od limitu zatwierdzone przez {k.dane['id_osoby']}" + (f": {uwagi}" if uwagi else "")
    if not zarezerwuj(s, d, z, ktx, f"{z.uzasadnienie or ''}\n{uzasadnienie}".strip()):
        # Zgoda, ale rezerwacja niemożliwa (budżet albo brak miejsc) – wniosek zamknięty, żeby sprawa nie utknęła:
        # delegowany może wybrać inny obiekt, a przełożony wie, dlaczego zgoda nie dała rezerwacji
        przyczyna = ktx.opis
        z.status = StatusRezerwacji.ANULOWANA
        z.uzasadnienie = f"{z.uzasadnienie or ''}\n{uzasadnienie}; rezerwacja niemożliwa: {przyczyna}".strip()
        ktx.decyzja(
            Decyzja(
                typ=TYP_ODSTEPSTWA,
                wybrany_wariant=f"Zgoda bez rezerwacji: {nazwa}",
                uzasadnienie=f"{uzasadnienie}; {przyczyna}",
                decyzja_finansowa=True,
            )
        )
        for odbiorca, dalej in (
            (d.id_zolnierza, "Wybierz inny obiekt zakwaterowania."),
            (k.dane["id_osoby"], "Zdecyduj, jak zorganizować nocleg delegowanego."),
        ):
            ktx.wyslij(
                Typ.POWIADOMIENIE,
                {
                    "do": odbiorca,
                    "tresc": f"Delegacja {d.id_delegacji}: mimo zgody na odstępstwo nie zarezerwowano {nazwa} "
                    f"({przyczyna}). {dalej}",
                },
            )
        return
    ktx.decyzja(
        Decyzja(
            typ=TYP_ODSTEPSTWA,
            wybrany_wariant=f"Zgoda: {nazwa}",
            uzasadnienie=uzasadnienie + f"; koszt {_kwota(z.koszt_calkowity)}",
            decyzja_finansowa=True,
        )
    )
    ktx.wyslij(
        Typ.POWIADOMIENIE,
        {
            "do": d.id_zolnierza,
            "tresc": f"Delegacja {d.id_delegacji}: przełożony zgodził się na odstępstwo – zarezerwowano pierwotnie "
            f"wybrany obiekt {nazwa}",
        },
    )
