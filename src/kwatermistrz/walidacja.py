"""Reguły walidacji wniosku (epika „Wnioski i walidacja danych”, S22IT1S0P2-7…-12)."""

from dataclasses import dataclass
from datetime import date, datetime

FORMATY_DATY = ("%Y-%m-%d", "%d.%m.%Y")

# S22IT1S0P2-12: pola wymagane dla danego środka transportu.
# Lista wymaga uzgodnienia w zespole – dopóki jej nie ma, system stosuje tylko zatwierdzone warunki.
WYMAGANE_POLA_TRANSPORTU: dict[str, dict[str, str]] = {
    "samochód prywatny": {
        "nr_rejestracyjny": "numer rejestracyjny",
        "pojemnosc_silnika": "pojemność silnika",
    },
}


@dataclass(frozen=True)
class BladWalidacji:
    pole: str
    komunikat: str
    historyjka: str


def parsuj_date(tekst: str) -> date | None:
    for fmt in FORMATY_DATY:
        try:
            return datetime.strptime(tekst.strip(), fmt).date()
        except ValueError:
            continue
    return None


def _pusty(wartosc) -> bool:
    return wartosc is None or not str(wartosc).strip()


def waliduj_wniosek(wniosek: dict, jednostka: str | None) -> list[BladWalidacji]:
    bledy: list[BladWalidacji] = []

    if _pusty(wniosek.get("cel_wyjazdu")):
        bledy.append(BladWalidacji("cel_wyjazdu", "Uzupełnij cel wyjazdu", "S22IT1S0P2-7"))
    if _pusty(jednostka):
        bledy.append(BladWalidacji("jednostka", "Uzupełnij jednostkę wojskową", "S22IT1S0P2-9"))
    if _pusty(wniosek.get("miejsce_docelowe")):
        bledy.append(BladWalidacji("miejsce_docelowe", "Uzupełnij miejsce docelowe wyjazdu", "S22IT1S0P2-11"))

    daty: dict[str, date] = {}
    for pole, nazwa in (("data_wyjazdu", "wyjazdu"), ("data_powrotu", "powrotu")):
        tekst = wniosek.get(pole)
        if _pusty(tekst):
            bledy.append(BladWalidacji(pole, f"Uzupełnij datę {nazwa}", "S22IT1S0P2-10"))
        elif (d := parsuj_date(str(tekst))) is None:
            bledy.append(
                BladWalidacji(
                    pole, f"Nieprawidłowy format daty {nazwa} – użyj RRRR-MM-DD lub DD.MM.RRRR", "S22IT1S0P2-10"
                )
            )
        else:
            daty[pole] = d
    # Porównanie dat tylko, gdy obie są poprawne; wyjazd i powrót tego samego dnia są dopuszczalne
    if len(daty) == 2 and daty["data_powrotu"] < daty["data_wyjazdu"]:
        bledy.append(
            BladWalidacji("data_powrotu", "Data powrotu nie może być wcześniejsza niż data wyjazdu", "S22IT1S0P2-8")
        )

    srodek = (wniosek.get("srodek_transportu") or "").strip().lower()
    dane_transportu = wniosek.get("dane_transportu") or {}
    for pole, opis in WYMAGANE_POLA_TRANSPORTU.get(srodek, {}).items():
        if _pusty(dane_transportu.get(pole)):
            bledy.append(
                BladWalidacji(
                    f"dane_transportu.{pole}", f"Uzupełnij {opis} dla środka transportu: {srodek}", "S22IT1S0P2-12"
                )
            )

    return bledy
