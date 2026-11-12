"""Stawki i limity należności za podróże służbowe – jedno miejsce dla wszystkich agentów.

Wartości przyjęte w modelu informacyjnym projektu. Przed użyciem w rozliczeniach (S22IT1S0P2-36, -43)
zweryfikuj je z aktualnymi przepisami o należnościach za podróże służbowe żołnierzy.
"""

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
