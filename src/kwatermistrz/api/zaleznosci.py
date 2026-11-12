"""Wspólne zależności endpointów: sesja bazy, broker, pobieranie delegacji."""

from collections.abc import Iterator

from fastapi import HTTPException, Request
from sqlalchemy.orm import Session

from ..db import sesja
from ..messaging import Broker
from ..models import Delegacja


def get_sesja() -> Iterator[Session]:
    with sesja() as s:
        yield s


def get_broker(request: Request) -> Broker:
    return request.app.state.broker


def pobierz_delegacje(s: Session, id_delegacji: str) -> Delegacja:
    d = s.get(Delegacja, id_delegacji)
    if d is None:
        raise HTTPException(404, f"Nie znaleziono delegacji {id_delegacji}")
    return d
