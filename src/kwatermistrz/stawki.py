"""Stawki i limity należności za podróże służbowe – jedno miejsce dla wszystkich agentów.

Wartości przyjęte w modelu informacyjnym projektu. Przed użyciem w rozliczeniach (S22IT1S0P2-36, -43)
zweryfikuj je z aktualnymi przepisami o należnościach za podróże służbowe żołnierzy.
"""

from datetime import date
from decimal import Decimal

DIETA_KRAJOWA = Decimal("45.00")  # za dobę podróży
LIMIT_NOCLEGOWY = Decimal("300.00")  # za dobę, zwrot na podstawie rachunku
RYCZALT_NOCLEG = DIETA_KRAJOWA * Decimal("1.5")  # nocleg bez rachunku: 150% diety
RYCZALT_DOJAZDY = DIETA_KRAJOWA * Decimal("0.2")  # komunikacja miejscowa: 20% diety za dobę

# Pomniejszenie diety za każdy zapewniony posiłek (część diety)
POTRACENIE_ZA_POSILEK = {
    "śniadanie": Decimal("0.25"),
    "obiad": Decimal("0.50"),
    "kolacja": Decimal("0.25"),
}


# Szacunkowy koszt przejazdu tam i z powrotem – do oceny budżetu przed zatwierdzeniem (S22IT1S0P2-35).
# Wartość przykładowa; dokładny koszt wybiera Agent Transportu (S22IT1S0P2-27).
SZACUNEK_PRZEJAZDU = Decimal("200.00")


def szacuj_koszt(data_wyjazdu: date | None, data_powrotu: date | None, nocleg: bool) -> Decimal | None:
    """Przewidywany koszt delegacji: dieta i komunikacja miejscowa za każdą dobę, przejazd, noclegi w limicie."""
    if data_wyjazdu is None or data_powrotu is None or data_powrotu < data_wyjazdu:
        return None
    doby = (data_powrotu - data_wyjazdu).days + 1
    koszt = doby * (DIETA_KRAJOWA + RYCZALT_DOJAZDY) + SZACUNEK_PRZEJAZDU
    if nocleg:
        koszt += (doby - 1) * LIMIT_NOCLEGOWY
    return koszt.quantize(Decimal("0.01"))
