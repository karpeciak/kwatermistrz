"""S22IT1S0P2-20 – wykrywanie i blokowanie duplikatów dokumentów kosztowych."""

from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from unicodedata import normalize

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import DokumentKosztowy


def klucz_dokumentu(rodzaj: str, numer: str, data: date, kwota: Decimal) -> str:
    """Buduje stabilny, globalny klucz z rodzaju, numeru, daty i kwoty dokumentu."""

    def tekst(wartosc: str) -> str:
        return " ".join(normalize("NFKC", wartosc).split()).casefold()

    kwota_do_grosza = kwota.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return f"{tekst(rodzaj)}|{tekst(numer)}|{data.isoformat()}|{kwota_do_grosza:.2f}"


def znajdz_duplikat(s: Session, klucz: str) -> DokumentKosztowy | None:
    """Wyszukuje dokument po kluczu we wszystkich rozliczeniach."""
    return s.scalar(select(DokumentKosztowy).where(DokumentKosztowy.klucz_unikalnosci == klucz))
