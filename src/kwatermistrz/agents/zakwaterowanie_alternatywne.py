"""Zapewnienie zakwaterowania alternatywnego w granicach limitu – S22IT1S0P2-15 (podzadania 130–133).

Obsługę wywołuje Agent Zakwaterowania (AG-05):
1. Dobór propozycji (`zakwaterowanie.przygotuj_propozycje`, S22IT1S0P2-39) – gdy w obiektach wojskowych w miejscu
   delegacji nie ma wolnych miejsc z terminowym dojazdem (`wojskowe_dostepne`), system informuje o tym delegowanego
   i przedstawia obiekty cywilne mieszczące się w limicie noclegowym (koszt, czas dojazdu) – podzadanie 130, 132.
2. Rezerwacja (`limit_noclegu.zarezerwuj`, S22IT1S0P2-40 – zgodność z limitem i budżetem jednostki, podzadanie 131)
   uzupełnia dokumentację delegacji: dane noclegu i uzasadnienie wyboru trafiają do polecenia wyjazdu
   (`uzupelnij_dokumentacje`); wybór obiektu cywilnego przy braku miejsc wojskowych ma uzasadnienie
   (`uzasadnienie_wyboru`).
3. Delegowany odrzuca wszystkie obiekty (3a–4a) → `wybor.odrzucenie_zakwaterowania` → `odrzuc_propozycje`:
   propozycje `DO_UZGODNIENIA`, sprawa u osoby odpowiedzialnej za organizację delegacji, informacja dla delegowanego.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..audit import Decyzja
from ..db import teraz
from ..messaging import Komunikat, Typ
from ..models import (
    Delegacja,
    ObiektZakwaterowania,
    PolecenieWyjazdu,
    Przedsiewziecie,
    Status,
    StatusRezerwacji,
    Zakwaterowanie,
)
from ..stawki import LIMIT_NOCLEGOWY
from .base import BladProcesu, Kontekst, pobierz_delegacje
from .zakwaterowanie import maks_czas_dojazdu, terminowy_dojazd

TYP_ODRZUCENIA = "Odrzucenie propozycji zakwaterowania"
WOJSKOWY = "Wojskowy"


def _przedsiewziecie(s: Session, d: Delegacja) -> Przedsiewziecie | None:
    return s.get(Przedsiewziecie, d.id_przedsiewziecia) if d.id_przedsiewziecia else None


def miejscowosc_delegacji(s: Session, d: Delegacja) -> str | None:
    p = _przedsiewziecie(s, d)
    return p.miejscowosc if p else d.miejsce_docelowe


def wojskowe_dostepne(s: Session, d: Delegacja) -> bool:
    """Czy w miejscu delegacji jest obiekt wojskowy z wolnym miejscem i dojazdem na czas na zajęcia."""
    p = _przedsiewziecie(s, d)
    maks_min = maks_czas_dojazdu(p.godzina_rozpoczecia if p else None)
    wojskowe = s.scalars(
        select(ObiektZakwaterowania).where(
            ObiektZakwaterowania.miejscowosc == miejscowosc_delegacji(s, d),
            ObiektZakwaterowania.rodzaj == WOJSKOWY,
            ObiektZakwaterowania.liczba_wolnych_miejsc > 0,
        )
    ).all()
    return any(terminowy_dojazd(o, maks_min) for o in wojskowe)


def komunikat_brak_wojskowych(miejscowosc: str | None) -> str:
    return (
        f"Brak wolnych miejsc w obiektach wojskowych w miejscu delegacji ({miejscowosc}) i w jego pobliżu – "
        f"przedstawiono obiekty cywilne mieszczące się w limicie noclegowym {LIMIT_NOCLEGOWY} zł za dobę"
    )


def uzasadnienie_wyboru(s: Session, d: Delegacja, obiekt: ObiektZakwaterowania | None, wlasne: str) -> str:
    """Wybór obiektu cywilnego przy braku miejsc wojskowych ma udokumentowane uzasadnienie (warunek końcowy)."""
    if obiekt is None or obiekt.rodzaj == WOJSKOWY or wojskowe_dostepne(s, d):
        return wlasne
    powod = (
        f"Obiekt cywilny – brak wolnych miejsc w obiektach wojskowych w {miejscowosc_delegacji(s, d)}; "
        f"cena {obiekt.cena_za_dobe:.2f} zł za dobę"
        + (f" w limicie {LIMIT_NOCLEGOWY} zł" if obiekt.cena_za_dobe <= LIMIT_NOCLEGOWY else "")
    )
    return f"{powod}. {wlasne}" if wlasne else powod


def uzupelnij_dokumentacje(s: Session, d: Delegacja, z: Zakwaterowanie, obiekt: ObiektZakwaterowania | None) -> bool:
    """Dane noclegu i uzasadnienie wyboru w poleceniu wyjazdu (S22IT1S0P2-38)."""
    polecenie = s.scalar(select(PolecenieWyjazdu).where(PolecenieWyjazdu.id_delegacji == d.id_delegacji))
    if polecenie is None:
        return False
    tresc = dict(polecenie.tresc or {})  # nowy słownik – zmiana kolumny JSON musi być widoczna dla sesji
    tresc["zakwaterowanie"] = {
        "obiekt": obiekt.nazwa if obiekt else z.id_obiektu,
        "rodzaj": obiekt.rodzaj if obiekt else None,
        "miejscowosc": obiekt.miejscowosc if obiekt else None,
        "od": z.data_od.isoformat(),
        "do": z.data_do.isoformat(),
        "cena_za_dobe": f"{z.cena_za_dobe:.2f}",
        "koszt": f"{z.koszt_calkowity:.2f}",
        "uzasadnienie": z.uzasadnienie,
        "data_rezerwacji": teraz().isoformat(timespec="minutes"),
    }
    polecenie.tresc = tresc
    return True


# ---------------------------------------------------------------- 3a–4a: odrzucenie wszystkich obiektów


def odrzuc_propozycje(s: Session, k: Komunikat, ktx: Kontekst) -> None:
    from .limit_noclegu import ZAJETE, noclegi, przelozony  # import lokalny – limit_noclegu korzysta z tego modułu

    d = pobierz_delegacje(s, k.id_delegacji)
    if k.dane.get("id_osoby") != d.id_zolnierza:
        raise BladProcesu("Propozycje zakwaterowania odrzuca delegowany")
    przyczyna = str(k.dane.get("przyczyna") or "").strip()
    if not przyczyna:
        raise BladProcesu("Podaj przyczynę odrzucenia wszystkich obiektów")
    if d.status != Status.ZATWIERDZONY or noclegi(s, d, *ZAJETE):
        ktx.ostrzezenie("Zakwaterowanie zostało już wybrane albo delegacja nie jest na etapie organizacji")
        return
    propozycje = noclegi(s, d, StatusRezerwacji.PROPOZYCJA, StatusRezerwacji.WSTRZYMANA)
    if not propozycje:
        ktx.opis = "Brak propozycji do odrzucenia – decyzja została już zapisana"
        return
    for z in propozycje:
        z.status = StatusRezerwacji.DO_UZGODNIENIA
        z.uzasadnienie = f"Odrzucone przez delegowanego: {przyczyna}"
    odpowiedzialny = przelozony(s, d)
    ktx.ostrzezenie(f"Delegowany odrzucił wszystkie obiekty ({len(propozycje)}): {przyczyna} – sprawa przekazana")
    ktx.decyzja(
        Decyzja(
            typ=TYP_ODRZUCENIA,
            wybrany_wariant="Do uzgodnienia",
            uzasadnienie=przyczyna,
            liczba_wariantow=len(propozycje),
        )
    )
    if odpowiedzialny:
        ktx.wyslij(
            Typ.POWIADOMIENIE,
            {
                "do": odpowiedzialny,
                "tresc": f"Delegacja {d.id_delegacji}: delegowany odrzucił proponowane obiekty zakwaterowania "
                f"({przyczyna}). Zorganizuj nocleg w granicach limitu indywidualnie.",
            },
        )
    ktx.wyslij(
        Typ.POWIADOMIENIE,
        {
            "do": d.id_zolnierza,
            "tresc": f"Delegacja {d.id_delegacji}: sprawę zakwaterowania przekazano osobie odpowiedzialnej "
            f"za organizację delegacji{f' ({odpowiedzialny})' if odpowiedzialny else ''}",
        },
    )
