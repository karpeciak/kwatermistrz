"""API Kwatermistrza – aplikacja FastAPI składana z modułów według obszarów procesu.

| Moduł            | Historyjki sprintu 1      |
|------------------|---------------------------|
| wnioski.py       | S22IT1S0P2-33, -37, -32   |
| zatwierdzenia.py | S22IT1S0P2-14, -35        |
| organizacja.py   | S22IT1S0P2-38, -27, -39   |
| rozliczenia.py   | S22IT1S0P2-36, -20, -43   |
"""

from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import __version__
from ..config import settings
from ..db import migruj, sesja
from ..messaging import Broker
from ..models import Zolnierz
from ..seed import zaladuj_dane_poczatkowe
from ..web import strony
from . import organizacja, rozliczenia, wnioski, zatwierdzenia
from .schemas import ZolnierzOut
from .zaleznosci import get_broker, get_sesja

__all__ = ["app", "get_broker", "get_sesja"]


@asynccontextmanager
async def lifespan(app: FastAPI):
    migruj()
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
for modul in (wnioski, zatwierdzenia, organizacja, rozliczenia):
    app.include_router(modul.router)

# Interfejs użytkownika (strony pod /app) – src/kwatermistrz/web/
app.include_router(strony.router)
app.mount("/statyczne", StaticFiles(directory=strony.KATALOG / "statyczne"), name="statyczne")


@app.get("/", include_in_schema=False)
def strona_glowna():
    return RedirectResponse("/app", status_code=303)


@app.get("/health", tags=["System"])
def health() -> dict:
    return {"status": "ok", "wersja": __version__}


@app.get("/zolnierze", response_model=list[ZolnierzOut], tags=["System"])
def lista_zolnierzy(s: Session = Depends(get_sesja)):
    return s.scalars(select(Zolnierz).order_by(Zolnierz.id_zolnierza)).all()
