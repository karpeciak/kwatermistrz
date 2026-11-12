"""S22IT1S0P2-38 – Wygenerowanie polecenia wyjazdu służbowego po zatwierdzeniu wniosku.

Wywoływane przez Agenta Wniosków po komunikacie `zadanie.polecenie` (Koordynator wysyła go po zatwierdzeniu).

Do zrobienia:
1. Zebrać dane według wzoru: delegowany (stopień, imię i nazwisko, jednostka), cel, miejsce, termin,
   podstawa (numer i data rozkazu), przełożony zatwierdzający i data decyzji.
2. Brak danych wymaganych we wzorze (scenariusz 2a) → `ktx.ostrzezenie(...)` i powiadomienie osoby
   organizującej delegację; polecenia nie zapisujemy.
3. Zapisać `PolecenieWyjazdu` z kolejnym numerem (np. PW/2026/0001) i treścią (`tresc`) wypełnioną według wzoru.
4. `ktx.wyslij(Typ.WYNIK_POLECENIA, {"numer": ...})` i powiadomić delegowanego.
5. Endpoint udostępniający dokument: `api/organizacja.py`.
"""

from sqlalchemy.orm import Session

from ..messaging import Komunikat
from .base import Kontekst, do_zrobienia, pobierz_delegacje

WERSJA_WZORU = "2026-1"


def sporzadz_polecenie(s: Session, k: Komunikat, ktx: Kontekst) -> None:
    pobierz_delegacje(s, k.id_delegacji)
    do_zrobienia(ktx, "S22IT1S0P2-38")
