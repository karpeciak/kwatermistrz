"""API Kwatermistrza – przyjmuje wnioski i decyzje, udostępnia stan delegacji i przebieg pracy agentów."""

import uuid
from collections.abc import Iterator
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import __version__
from ..config import settings
from ..db import sesja, teraz, utworz_schemat
from ..messaging import Broker, Komunikat, Typ
from ..models import Delegacja, LogDecyzji, LogOperacji, Przedsiewziecie, Zolnierz
from ..seed import zaladuj_dane_poczatkowe
from .schemas import DecyzjaAgentaOut, DecyzjaIn, DelegacjaOut, OperacjaOut, WniosekIn, ZolnierzOut


@asynccontextmanager
async def lifespan(app: FastAPI):
    utworz_schemat()
    with sesja() as s:
        zaladuj_dane_poczatkowe(s)
    broker = None
    if settings.polacz_broker:
        broker = Broker(settings.amqp_url, settings.exchange)
        await broker.polacz()
    app.state.broker = broker
    yield
    if broker:
        await broker.zamknij()


app = FastAPI(title="Kwatermistrz API", version=__version__, lifespan=lifespan)


def get_sesja() -> Iterator[Session]:
    with sesja() as s:
        yield s


def get_broker(request: Request) -> Broker:
    return request.app.state.broker


def pobierz(s: Session, id_delegacji: str) -> Delegacja:
    d = s.get(Delegacja, id_delegacji)
    if d is None:
        raise HTTPException(404, f"Nie znaleziono delegacji {id_delegacji}")
    return d


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "wersja": __version__}


@app.get("/zolnierze", response_model=list[ZolnierzOut])
def lista_zolnierzy(s: Session = Depends(get_sesja)):
    return s.scalars(select(Zolnierz).order_by(Zolnierz.id_zolnierza)).all()


@app.post("/wnioski", status_code=202, response_model=DelegacjaOut)
async def zloz_wniosek(
    wniosek: WniosekIn,
    s: Session = Depends(get_sesja),
    broker: Broker = Depends(get_broker),
    idempotency_key: str | None = Header(None),
):
    # S22IT1S0P2-22: ponowienie po utracie połączenia nie tworzy drugiego wniosku
    if idempotency_key:
        istniejaca = s.scalar(select(Delegacja).where(Delegacja.klucz_idempotencji == idempotency_key))
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
        klucz_idempotencji=idempotency_key,
    )
    s.add(d)
    s.commit()
    await broker.publikuj(Komunikat(typ=Typ.WNIOSEK_ZLOZONY, id_delegacji=d.id_delegacji))
    return d


@app.get("/delegacje/{id_delegacji}", response_model=DelegacjaOut)
def pokaz_delegacje(id_delegacji: str, s: Session = Depends(get_sesja)):
    return pobierz(s, id_delegacji)


@app.post("/delegacje/{id_delegacji}/decyzja", status_code=202)
async def decyzja_przelozonego(
    id_delegacji: str,
    decyzja: DecyzjaIn,
    s: Session = Depends(get_sesja),
    broker: Broker = Depends(get_broker),
):
    pobierz(s, id_delegacji)
    await broker.publikuj(Komunikat(typ=Typ.DECYZJA_PRZELOZONEGO, id_delegacji=id_delegacji, dane=decyzja.model_dump()))
    return {"przyjeto": True}


@app.get("/delegacje/{id_delegacji}/przebieg", response_model=list[OperacjaOut])
def przebieg_delegacji(id_delegacji: str, s: Session = Depends(get_sesja)):
    """Log operacji i decyzji agentów dla jednej delegacji – ślad audytowy procesu."""
    pobierz(s, id_delegacji)
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
