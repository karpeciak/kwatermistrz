"""Organizacja wyjazdu – S22IT1S0P2-38 (polecenie wyjazdu), -27 (wybór wariantu podróży), -39 (zakwaterowanie).

Do zrobienia w sprincie 1:
- S22IT1S0P2-38: `GET /delegacje/{id}/polecenie` – polecenie wyjazdu dla delegowanego (`PolecenieWyjazdu`).
- S22IT1S0P2-27: `GET /delegacje/{id}/warianty-podrozy` – propozycje z rekomendacją (`Przejazd`, status propozycja),
  `POST /delegacje/{id}/wybor-podrozy` – wybór wariantu z przyczyną albo odrzucenie wszystkich
  → komunikat `Typ.WYBOR_TRANSPORTU`.
- S22IT1S0P2-39: `GET /delegacje/{id}/zakwaterowanie` – propozycje obiektów (koszt, czas dojazdu),
  `POST /delegacje/{id}/wybor-zakwaterowania` – wybór obiektu (z uzasadnieniem przy odstępstwie)
  → komunikat `Typ.WYBOR_ZAKWATEROWANIA`.
"""

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from ..messaging import Broker, Komunikat, Typ
from ..models import Zolnierz
from ..podroze import (
    BladPodrozy,
    Kryterium,
    WariantPodrozyIn,
    WyborPodrozy,
    sprawdz_decyzje,
    sprawdz_dodanie_wariantu,
    zestawienie,
)
from .zaleznosci import dostep_do_podrozy, get_broker, get_sesja, sprawdz_csrf, uzytkownik_sesji

router = APIRouter(tags=["Organizacja wyjazdu"])


@router.get("/delegacje/{id_delegacji}/warianty-podrozy")
def warianty_podrozy(
    id_delegacji: str,
    kryterium: Kryterium = "standardowe",
    s: Session = Depends(get_sesja),
    uzytkownik: Zolnierz = Depends(uzytkownik_sesji),
):
    return zestawienie(s, dostep_do_podrozy(s, id_delegacji, uzytkownik), kryterium)


async def przyjmij_wybor(s: Session, broker: Broker, id_delegacji: str, u: Zolnierz, wybor: WyborPodrozy):
    d = dostep_do_podrozy(s, id_delegacji, u, zapis=True)
    try:
        sprawdz_decyzje(s, d, wybor)
    except BladPodrozy as e:
        raise HTTPException(409, str(e)) from e
    await broker.publikuj(
        Komunikat(
            typ=Typ.WYBOR_TRANSPORTU,
            id_delegacji=id_delegacji,
            dane={**wybor.model_dump(), "id_osoby": u.id_zolnierza},
        )
    )
    return {"przyjeto": True, "komunikat": "Decyzja przekazana do agenta transportu"}


async def przyjmij_wariant(s: Session, broker: Broker, id_delegacji: str, u: Zolnierz, wariant: WariantPodrozyIn):
    d = dostep_do_podrozy(s, id_delegacji, u, zapis=True)
    try:
        sprawdz_dodanie_wariantu(s, d)
    except BladPodrozy as e:
        raise HTTPException(409, str(e)) from e
    await broker.publikuj(
        Komunikat(
            typ=Typ.PROPOZYCJA_TRANSPORTU,
            id_delegacji=id_delegacji,
            dane={**wariant.model_dump(mode="json"), "id_osoby": u.id_zolnierza},
        )
    )
    return {"przyjeto": True}


@router.post("/delegacje/{id_delegacji}/warianty-podrozy", status_code=202)
async def dodaj_wariant_podrozy(
    id_delegacji: str,
    wariant: WariantPodrozyIn,
    request: Request,
    s: Session = Depends(get_sesja),
    broker: Broker = Depends(get_broker),
    uzytkownik: Zolnierz = Depends(uzytkownik_sesji),
):
    sprawdz_csrf(request, request.headers.get("X-CSRF-Token"))
    return await przyjmij_wariant(s, broker, id_delegacji, uzytkownik, wariant)


@router.post("/delegacje/{id_delegacji}/wybor-podrozy", status_code=202)
async def wybor_podrozy(
    id_delegacji: str,
    wybor: WyborPodrozy,
    request: Request,
    s: Session = Depends(get_sesja),
    broker: Broker = Depends(get_broker),
    uzytkownik: Zolnierz = Depends(uzytkownik_sesji),
):
    """Sesja z /app/logowanie; X-CSRF-Token z formularza strony podróży."""
    sprawdz_csrf(request, request.headers.get("X-CSRF-Token"))
    return await przyjmij_wybor(s, broker, id_delegacji, uzytkownik, wybor)
