"""Wspólne zależności endpointów: sesja bazy, broker, pobieranie delegacji."""

from collections.abc import Iterator

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from ..config import settings
from ..db import sesja
from ..messaging import Broker
from ..models import Delegacja, Zolnierz
from ..sesje import CIASTECZKO, odczytaj_sesje, poprawny_csrf


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


def uzytkownik_sesji(request: Request, s: Session = Depends(get_sesja)) -> Zolnierz:
    osoba = odczytaj_sesje(request.cookies.get(CIASTECZKO), settings.session_secret.get_secret_value())
    u = s.get(Zolnierz, osoba) if osoba else None
    if u is None:
        raise HTTPException(401, "Zaloguj się w /app/logowanie")
    return u


def sprawdz_csrf(request: Request, token: str | None) -> None:
    if not isinstance(token, str):
        token = None
    if not poprawny_csrf(request.cookies.get(CIASTECZKO), token, settings.session_secret.get_secret_value()):
        raise HTTPException(403, "Nieprawidłowy token formularza; odśwież stronę")


async def chron_formularze(request: Request) -> None:
    """Wspólna ochrona zapisów przez zalogowane formularze /app, także HTMX.

    Brak sesji obsługuje zależność uwierzytelnienia konkretnego endpointu.
    Logowanie demonstracyjne nie wymaga wcześniejszego zalogowania.
    """
    if request.method in {"GET", "HEAD", "OPTIONS"} or request.url.path == "/app/logowanie":
        return
    if odczytaj_sesje(request.cookies.get(CIASTECZKO), settings.session_secret.get_secret_value()) is None:
        return
    token = request.headers.get("X-CSRF-Token")
    rodzaj = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if not token and rodzaj in {"application/x-www-form-urlencoded", "multipart/form-data"}:
        token = (await request.form()).get("csrf")
    sprawdz_csrf(request, token)


def dostep_do_podrozy(s: Session, id_delegacji: str, u: Zolnierz, zapis: bool = False) -> Delegacja:
    d = pobierz_delegacje(s, id_delegacji)
    if u.id_zolnierza not in (d.id_zolnierza, d.id_zatwierdzajacego):
        raise HTTPException(404, "Nie znaleziono delegacji")
    if zapis and u.id_zolnierza != d.id_zatwierdzajacego:
        raise HTTPException(403, "Wariant podróży wybiera osoba akceptująca podróż")
    return d
