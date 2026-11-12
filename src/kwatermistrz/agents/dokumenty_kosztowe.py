"""S22IT1S0P2-20 – Wykrywanie i blokowanie ponownego rozliczenia tego samego dokumentu.

Używane przy przyjmowaniu dokumentów kosztowych (API `api/rozliczenia.py`) i w Agencie Rozliczeń.
Baza ma unikalny indeks na `DokumentKosztowy.klucz_unikalnosci`, więc duplikat nie zostanie zapisany
nawet przy równoczesnym przesłaniu – ale użytkownik musi dostać czytelną informację o przyczynie odrzucenia.

Do zrobienia:
1. `klucz_dokumentu` – ustalić, które pola jednoznacznie identyfikują dokument (np. rodzaj, numer, data, kwota),
   znormalizować je (wielkość liter, spacje, format kwoty).
2. `znajdz_duplikat` – zwrócić wcześniej zarejestrowany dokument o tym samym kluczu (także z innej delegacji).
3. W API: przy duplikacie odrzucić dokument z komunikatem o przyczynie (scenariusz krok 4), nowy dokument
   przypisać do rozliczenia (3a–4a). Odrzucenie zapisać w logu operacji.
"""

from datetime import date
from decimal import Decimal

from sqlalchemy.orm import Session

from ..models import DokumentKosztowy


def klucz_dokumentu(rodzaj: str, numer: str, data: date, kwota: Decimal) -> str:
    """Wstępna wersja klucza – do dopracowania w S22IT1S0P2-20."""
    return f"{rodzaj}|{numer}|{data.isoformat()}|{kwota:.2f}"


def znajdz_duplikat(s: Session, klucz: str) -> DokumentKosztowy | None:
    raise NotImplementedError("S22IT1S0P2-20")
