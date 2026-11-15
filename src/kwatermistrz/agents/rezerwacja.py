"""Zapis wybranego zakwaterowania – S22IT1S0P2-39, podzadanie S22IT1S0P2-99.

Wywołuje go Agent Zakwaterowania po komunikacie `wybor.zakwaterowanie` z ekranu `/app/wnioski/{id}/zakwaterowanie`
(dane: `id_zakwaterowania`, `uzasadnienie`):
- obiekt w limicie: rezerwacja miejsca, status `WYBRANA`, uzasadnienie wyboru, `wynik.zakwaterowanie`
  dla Koordynatora,
- obiekt wymagający odstępstwa (scenariusz 3a–4a): status `DO_DECYZJI_PRZELOZONEGO` z uzasadnieniem
  delegowanego, powiadomienie przełożonego i delegowanego.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..audit import Decyzja
from ..messaging import Komunikat, Typ
from ..models import ObiektZakwaterowania, StatusRezerwacji, Zakwaterowanie, Zolnierz
from .base import Kontekst, pobierz_delegacje

TYP_DECYZJI = "Wybór zakwaterowania"


def zapisz_wybor_zakwaterowania(s: Session, k: Komunikat, ktx: Kontekst) -> None:
    d = pobierz_delegacje(s, k.id_delegacji)
    z = s.get(Zakwaterowanie, k.dane.get("id_zakwaterowania"))
    if z is None or z.id_delegacji != d.id_delegacji:
        ktx.ostrzezenie("Nie znaleziono wskazanej propozycji zakwaterowania – wybór pominięty")
        return
    if z.status != StatusRezerwacji.PROPOZYCJA:
        ktx.ostrzezenie(f"Propozycja ma status „{z.status}” – wybór pominięty")
        return
    juz_wybrane = s.scalar(
        select(Zakwaterowanie).where(
            Zakwaterowanie.id_delegacji == d.id_delegacji,
            Zakwaterowanie.status.in_([StatusRezerwacji.WYBRANA, StatusRezerwacji.DO_DECYZJI_PRZELOZONEGO]),
        )
    )
    if juz_wybrane is not None:
        ktx.ostrzezenie("Zakwaterowanie dla tej delegacji zostało już wybrane – wybór pominięty")
        return

    obiekt = s.get(ObiektZakwaterowania, z.id_obiektu)
    nazwa = obiekt.nazwa if obiekt else z.id_obiektu
    uzasadnienie = str(k.dane.get("uzasadnienie") or "").strip()

    if z.wymaga_odstepstwa:
        if not uzasadnienie:
            ktx.ostrzezenie("Wybór wariantu wymagającego odstępstwa bez uzasadnienia – wybór pominięty")
            return
        z.status = StatusRezerwacji.DO_DECYZJI_PRZELOZONEGO
        z.uzasadnienie = uzasadnienie
        wnioskodawca = s.get(Zolnierz, d.id_zolnierza)
        przelozony = d.id_zatwierdzajacego or (wnioskodawca.id_przelozonego if wnioskodawca else None)
        if przelozony:
            tresc = f"Delegacja {d.id_delegacji}: zakwaterowanie {nazwa} wymaga odstępstwa od limitu – decyzja"
            ktx.wyslij(Typ.POWIADOMIENIE, {"do": przelozony, "tresc": tresc})
        tresc = f"Wybór zakwaterowania {nazwa} przekazano przełożonemu do decyzji"
        ktx.wyslij(Typ.POWIADOMIENIE, {"do": d.id_zolnierza, "tresc": tresc})
        ktx.opis = f"Wybrano {nazwa} – wariant wymaga odstępstwa, przekazano przełożonemu"
        ktx.decyzja(Decyzja(typ=TYP_DECYZJI, wybrany_wariant=nazwa, uzasadnienie=uzasadnienie, decyzja_finansowa=True))
        return

    if obiekt is not None and obiekt.liczba_wolnych_miejsc < 1:
        ktx.ostrzezenie(f"Brak wolnych miejsc w obiekcie {nazwa} – rezerwacja niemożliwa")
        return
    if obiekt is not None:
        obiekt.liczba_wolnych_miejsc -= 1
    z.status = StatusRezerwacji.WYBRANA
    z.uzasadnienie = uzasadnienie or (
        "Obiekt w limicie noclegowym"
        + (f", czas dojazdu na miejsce zadania {z.czas_dojazdu_min} min" if z.czas_dojazdu_min is not None else "")
    )
    ktx.opis = f"Zarezerwowano {nazwa}, {z.data_od:%d.%m}–{z.data_do:%d.%m}, {z.koszt_calkowity:.2f} zł"
    ktx.decyzja(Decyzja(typ=TYP_DECYZJI, wybrany_wariant=nazwa, uzasadnienie=z.uzasadnienie))
    ktx.wyslij(Typ.WYNIK_ZAKWATEROWANIA)
