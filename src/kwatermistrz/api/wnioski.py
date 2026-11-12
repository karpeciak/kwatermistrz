"""Wnioski – S22IT1S0P2-33 (rejestracja), -37 (wniosek roboczy), -32 (status i miejsce oczekiwania).

Do zrobienia w sprincie 1:
- S22IT1S0P2-33: dane wnioskodawcy z ewidencji (`Zolnierz`, `Jednostka`) w odpowiedzi, podstawa wyjazdu
  (`podstawa_numer_rozkazu`, `podstawa_data_rozkazu`), preferencje (`srodek_transportu`, `potrzeba_zakwaterowania`),
  podsumowanie przed złożeniem (walidacja bez rejestracji – `walidacja.waliduj_wniosek`).
- S22IT1S0P2-37: zapis wniosku roboczego (`Status.ROBOCZY`, `etap_przygotowania`, `data_ostatniej_zmiany`,
  `data_zlozenia` pusta), wznowienie, rezygnacja, złożenie wersji roboczej; widoczny tylko dla wnioskodawcy.
- S22IT1S0P2-32: lista wniosków wnioskodawcy, etap, osoba odpowiedzialna (`id_zatwierdzajacego`), czas oczekiwania
  (`data_zmiany_statusu`), historia (log operacji), informacja o przypomnieniu / przekazaniu zastępcy.
"""

import uuid

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import teraz
from ..messaging import Broker, Komunikat, Typ
from ..models import Delegacja, LogDecyzji, LogOperacji, Przedsiewziecie, Status, Zolnierz
from .schemas import DecyzjaAgentaOut, DelegacjaOut, OperacjaOut, WniosekIn
from .zaleznosci import get_broker, get_sesja, pobierz_delegacje

router = APIRouter(tags=["Wnioski"])


async def zarejestruj_wniosek(s: Session, broker: Broker, wniosek: WniosekIn, klucz: str | None = None) -> Delegacja:
    """Rejestruje złożony wniosek i przekazuje go agentom – wspólne dla API i interfejsu (`web/`)."""
    # S22IT1S0P2-22: ponowienie po utracie połączenia nie tworzy drugiego wniosku
    if klucz:
        istniejaca = s.scalar(select(Delegacja).where(Delegacja.klucz_idempotencji == klucz))
        if istniejaca:
            return istniejaca
    if s.get(Zolnierz, wniosek.id_zolnierza) is None:
        raise HTTPException(404, f"Nie znaleziono żołnierza {wniosek.id_zolnierza}")
    if wniosek.id_przedsiewziecia and s.get(Przedsiewziecie, wniosek.id_przedsiewziecia) is None:
        raise HTTPException(404, f"Nie znaleziono przedsięwzięcia {wniosek.id_przedsiewziecia}")

    d = Delegacja(
        id_delegacji=f"DEL-{teraz():%Y}-{uuid.uuid4().hex[:6].upper()}",
        id_zolnierza=wniosek.id_zolnierza,
        id_przedsiewziecia=wniosek.id_przedsiewziecia,
        dane_wniosku=wniosek.model_dump(exclude={"id_zolnierza", "id_przedsiewziecia"}),
        potrzeba_zakwaterowania=wniosek.potrzeba_zakwaterowania,
        status=Status.ZLOZONY,
        data_zlozenia=teraz(),
        klucz_idempotencji=klucz,
    )
    s.add(d)
    s.commit()
    await broker.publikuj(Komunikat(typ=Typ.WNIOSEK_ZLOZONY, id_delegacji=d.id_delegacji))
    return d


@router.post("/wnioski", status_code=202, response_model=DelegacjaOut)
async def zloz_wniosek(
    wniosek: WniosekIn,
    s: Session = Depends(get_sesja),
    broker: Broker = Depends(get_broker),
    idempotency_key: str | None = Header(None),
):
    return await zarejestruj_wniosek(s, broker, wniosek, idempotency_key)


@router.get("/delegacje/{id_delegacji}", response_model=DelegacjaOut)
def pokaz_delegacje(id_delegacji: str, s: Session = Depends(get_sesja)):
    return pobierz_delegacje(s, id_delegacji)


@router.get("/delegacje/{id_delegacji}/przebieg", response_model=list[OperacjaOut])
def przebieg_delegacji(id_delegacji: str, s: Session = Depends(get_sesja)):
    """Log operacji i decyzji agentów dla jednej delegacji – ślad audytowy procesu."""
    pobierz_delegacje(s, id_delegacji)
    operacje = s.scalars(
        select(LogOperacji).where(LogOperacji.id_delegacji == id_delegacji).order_by(LogOperacji.id_operacji)
    ).all()
    decyzje: dict[int, list[LogDecyzji]] = {}
    for d in s.scalars(select(LogDecyzji).where(LogDecyzji.id_operacji.in_([o.id_operacji for o in operacje]))):
        decyzje.setdefault(d.id_operacji, []).append(d)
    return [
        OperacjaOut.model_validate(o).model_copy(
            update={"decyzje": [DecyzjaAgentaOut.model_validate(d) for d in decyzje.get(o.id_operacji, [])]}
        )
        for o in operacje
    ]
