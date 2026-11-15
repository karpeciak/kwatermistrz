"""Organizacja wyjazdu – S22IT1S0P2-38 (polecenie wyjazdu), -27 (wybór wariantu podróży), -39 (zakwaterowanie).

Zrobione:
- S22IT1S0P2-38: `GET /delegacje/{id}/polecenie` – polecenie wyjazdu dla delegowanego (`PolecenieWyjazdu`).
- S22IT1S0P2-39: `GET /delegacje/{id}/zakwaterowanie` – propozycje obiektów (koszt, czas dojazdu),
  `POST /delegacje/{id}/wybor-zakwaterowania` – wybór obiektu (z uzasadnieniem przy odstępstwie)
  → komunikat `Typ.WYBOR_ZAKWATEROWANIA`.

- S22IT1S0P2-27: `GET /delegacje/{id}/warianty-podrozy` – propozycje z rekomendacją (`Przejazd`, status propozycja),
  `POST /delegacje/{id}/wybor-podrozy` – wybór wariantu z przyczyną albo odrzucenie wszystkich
  → komunikat `Typ.WYBOR_TRANSPORTU`.
"""

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..messaging import Broker, Komunikat, Typ
from ..models import ObiektZakwaterowania, PolecenieWyjazdu, Status, StatusRezerwacji, Zakwaterowanie, Zolnierz
from ..podroze import (
    BladPodrozy,
    Kryterium,
    WariantPodrozyIn,
    WyborPodrozy,
    sprawdz_decyzje,
    sprawdz_dodanie_wariantu,
    zestawienie,
)
from .zaleznosci import dostep_do_podrozy, get_broker, get_sesja, pobierz_delegacje, sprawdz_csrf, uzytkownik_sesji

router = APIRouter(tags=["Organizacja wyjazdu"])


@router.get("/delegacje/{id_delegacji}/warianty-podrozy")
def warianty_podrozy(
    id_delegacji: str,
    kryterium: Kryterium = "standardowe",
    s: Session = Depends(get_sesja),
    uzytkownik: Zolnierz = Depends(uzytkownik_sesji),
):
    return zestawienie(s, dostep_do_podrozy(s, id_delegacji, uzytkownik), kryterium)


async def przyjmij_wybor(
    s: Session, broker: Broker, id_delegacji: str, u: Zolnierz, wybor: WyborPodrozy, *, delegowany: bool = False
):
    d = dostep_do_podrozy(s, id_delegacji, u, zapis=not delegowany)
    if delegowany and u.id_zolnierza != d.id_zolnierza:
        raise HTTPException(403, "Ten ekran jest przeznaczony dla delegowanego")
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


# ---------------------------------------------------------------- S22IT1S0P2-38: polecenie wyjazdu


@router.get("/delegacje/{id_delegacji}/polecenie")
def polecenie_wyjazdu(id_delegacji: str, s: Session = Depends(get_sesja)):
    """Polecenie wyjazdu służbowego sporządzone przez Agenta Wniosków po zatwierdzeniu wniosku."""
    pobierz_delegacje(s, id_delegacji)
    p = s.scalar(select(PolecenieWyjazdu).where(PolecenieWyjazdu.id_delegacji == id_delegacji))
    if p is None:
        raise HTTPException(404, "Polecenie wyjazdu nie zostało jeszcze sporządzone")
    return {
        "numer": p.numer,
        "id_delegacji": p.id_delegacji,
        "data_sporzadzenia": p.data_sporzadzenia,
        "wersja_wzoru": p.wersja_wzoru,
        "tresc": p.tresc,
    }


# ---------------------------------------------------------------- S22IT1S0P2-39: zakwaterowanie


class WyborZakwaterowaniaIn(BaseModel):
    id_zakwaterowania: int
    uzasadnienie: str | None = Field(None, max_length=500)


@router.get("/delegacje/{id_delegacji}/zakwaterowanie")
def propozycje_zakwaterowania(id_delegacji: str, s: Session = Depends(get_sesja)):
    """Propozycje Agenta Zakwaterowania: obiekty w limicie, a gdy ich brak – warianty wymagające odstępstwa."""
    pobierz_delegacje(s, id_delegacji)
    noclegi = s.scalars(
        select(Zakwaterowanie)
        .where(Zakwaterowanie.id_delegacji == id_delegacji)
        .order_by(Zakwaterowanie.id_zakwaterowania)
    ).all()
    obiekty = {o.id_obiektu: o for o in s.scalars(select(ObiektZakwaterowania)).all()}
    return [
        {
            "id_zakwaterowania": z.id_zakwaterowania,
            "id_obiektu": z.id_obiektu,
            "nazwa": obiekty[z.id_obiektu].nazwa if z.id_obiektu in obiekty else None,
            "rodzaj": obiekty[z.id_obiektu].rodzaj if z.id_obiektu in obiekty else None,
            "data_od": z.data_od,
            "data_do": z.data_do,
            "cena_za_dobe": z.cena_za_dobe,
            "koszt_calkowity": z.koszt_calkowity,
            "czas_dojazdu_min": z.czas_dojazdu_min,
            "wymaga_odstepstwa": z.wymaga_odstepstwa,
            "status": z.status,
            "uzasadnienie": z.uzasadnienie,
        }
        for z in noclegi
    ]


@router.post("/delegacje/{id_delegacji}/wybor-zakwaterowania", status_code=202)
async def wybierz_zakwaterowanie(
    id_delegacji: str,
    wybor: WyborZakwaterowaniaIn,
    s: Session = Depends(get_sesja),
    broker: Broker = Depends(get_broker),
):
    """Wybór obiektu przez delegowanego; wariant z odstępstwem (3a) wymaga uzasadnienia i trafia do przełożonego."""
    d = pobierz_delegacje(s, id_delegacji)
    if d.status != Status.ZATWIERDZONY:
        raise HTTPException(409, "Zakwaterowanie wybiera się po zatwierdzeniu wniosku")
    z = s.get(Zakwaterowanie, wybor.id_zakwaterowania)
    if z is None or z.id_delegacji != id_delegacji:
        raise HTTPException(404, "Nie znaleziono propozycji zakwaterowania")
    juz_wybrane = s.scalar(
        select(Zakwaterowanie.id_zakwaterowania).where(
            Zakwaterowanie.id_delegacji == id_delegacji,
            Zakwaterowanie.status.in_([StatusRezerwacji.WYBRANA, StatusRezerwacji.DO_DECYZJI_PRZELOZONEGO]),
        )
    )
    if juz_wybrane is not None or z.status != StatusRezerwacji.PROPOZYCJA:
        raise HTTPException(409, "Zakwaterowanie zostało już wybrane")
    uzasadnienie = (wybor.uzasadnienie or "").strip()
    if z.wymaga_odstepstwa and not uzasadnienie:
        raise HTTPException(422, "Ten obiekt wymaga odstępstwa od limitu – podaj uzasadnienie wyboru")

    dane = {"id_zakwaterowania": z.id_zakwaterowania, "uzasadnienie": uzasadnienie or None}
    await broker.publikuj(Komunikat(typ=Typ.WYBOR_ZAKWATEROWANIA, id_delegacji=id_delegacji, dane=dane))
    return {"przyjeto": True}
