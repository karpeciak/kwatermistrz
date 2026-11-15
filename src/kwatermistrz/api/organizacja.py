"""Organizacja wyjazdu – S22IT1S0P2-38 (polecenie wyjazdu), -27 (wybór wariantu podróży), -39 (zakwaterowanie).

Do zrobienia w sprincie 1:
- S22IT1S0P2-38: `GET /delegacje/{id}/polecenie` – polecenie wyjazdu dla delegowanego (`PolecenieWyjazdu`).
- S22IT1S0P2-27: `GET /delegacje/{id}/warianty-podrozy` – propozycje z rekomendacją (`Przejazd`, status propozycja),
  `POST /delegacje/{id}/wybor-podrozy` – wybór wariantu z przyczyną albo odrzucenie wszystkich
  → komunikat `Typ.WYBOR_TRANSPORTU`.

Zrobione:
- S22IT1S0P2-39: `GET /delegacje/{id}/zakwaterowanie` – propozycje obiektów (koszt, czas dojazdu),
  `POST /delegacje/{id}/wybor-zakwaterowania` – wybór obiektu (z uzasadnieniem przy odstępstwie)
  → komunikat `Typ.WYBOR_ZAKWATEROWANIA`.
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..messaging import Broker, Komunikat, Typ
from ..models import ObiektZakwaterowania, Status, StatusRezerwacji, Zakwaterowanie
from .zaleznosci import get_broker, get_sesja, pobierz_delegacje

router = APIRouter(tags=["Organizacja wyjazdu"])


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
