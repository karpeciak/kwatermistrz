"""Zatwierdzanie – S22IT1S0P2-14 (jednokrotna decyzja), -35 (zbiorcze zatwierdzenie).

Do zrobienia w sprincie 1:
- S22IT1S0P2-35: zestawienie wniosków oczekujących na decyzję przełożonego (`Status.W_AKCEPTACJI`,
  `id_zatwierdzajacego`) z danymi wnioskodawcy, szacowanym kosztem (`szacowany_koszt`) i dostępnym budżetem
  jednostki (`budzet_delegacji` − `budzet_wykorzystany`); decyzja zbiorcza wysyła `decyzja.przelozonego` dla
  każdego wniosku (Agent Zatwierdzeń dba o jednokrotność – S22IT1S0P2-14); przekroczenie budżetu (3b–4b)
  wstrzymuje zatwierdzenie.
"""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..messaging import Broker, Komunikat, Typ
from .schemas import DecyzjaIn
from .zaleznosci import get_broker, get_sesja, pobierz_delegacje

router = APIRouter(tags=["Zatwierdzanie"])


@router.post("/delegacje/{id_delegacji}/decyzja", status_code=202)
async def decyzja_przelozonego(
    id_delegacji: str,
    decyzja: DecyzjaIn,
    s: Session = Depends(get_sesja),
    broker: Broker = Depends(get_broker),
):
    pobierz_delegacje(s, id_delegacji)
    await broker.publikuj(Komunikat(typ=Typ.DECYZJA_PRZELOZONEGO, id_delegacji=id_delegacji, dane=decyzja.model_dump()))
    return {"przyjeto": True}
