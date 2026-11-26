"""Interfejs ewidencji jednostek wojskowych – S22IT1S0P2-7."""

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..api.jednostki import JednostkaIn, zarejestruj_jednostke
from ..api.zaleznosci import chron_formularze, get_sesja, sprawdz_csrf
from ..models import Jednostka, Zolnierz
from .strony import biezacy_uzytkownik, strona

router = APIRouter(
    prefix="/app",
    include_in_schema=False,
    dependencies=[Depends(chron_formularze)],
)


def widok_jednostek(
    request: Request,
    s: Session,
    uzytkownik: Zolnierz,
    dane: dict | None = None,
    blad: str = "",
    dodano: bool = False,
    status: int = 200,
):
    jednostki = s.scalars(select(Jednostka).order_by(Jednostka.id_jednostki)).all()

    odpowiedz = strona(
        request,
        s,
        "jednostki.html",
        uzytkownik,
        jednostki=jednostki,
        dane=dane or {},
        blad=blad,
        dodano=dodano,
    )
    odpowiedz.status_code = status
    return odpowiedz


@router.get("/jednostki", response_class=HTMLResponse)
def lista_jednostek(
    request: Request,
    dodano: bool = False,
    s: Session = Depends(get_sesja),
    uzytkownik: Zolnierz = Depends(biezacy_uzytkownik),
):
    return widok_jednostek(request, s, uzytkownik, dodano=dodano)


@router.post("/jednostki", response_class=HTMLResponse)
def dodaj_jednostke(
    request: Request,
    id_jednostki: str = Form(""),
    nazwa: str = Form(""),
    miejscowosc: str = Form(""),
    csrf: str = Form(""),
    s: Session = Depends(get_sesja),
    uzytkownik: Zolnierz = Depends(biezacy_uzytkownik),
):
    sprawdz_csrf(request, csrf)

    formularz = {
        "id_jednostki": id_jednostki,
        "nazwa": nazwa,
        "miejscowosc": miejscowosc,
    }

    try:
        dane = JednostkaIn.model_validate(formularz)
    except ValidationError as exc:
        pole = str(exc.errors()[0]["loc"][0])
        return widok_jednostek(
            request,
            s,
            uzytkownik,
            dane=formularz,
            blad=f"Sprawdź poprawność pola: {pole}.",
            status=422,
        )

    try:
        zarejestruj_jednostke(dane, s)
    except HTTPException as exc:
        if exc.status_code != 409:
            raise
        return widok_jednostek(
            request,
            s,
            uzytkownik,
            dane=formularz,
            blad=str(exc.detail),
            status=409,
        )

    return RedirectResponse(
        "/app/jednostki?dodano=true",
        status_code=303,
    )
