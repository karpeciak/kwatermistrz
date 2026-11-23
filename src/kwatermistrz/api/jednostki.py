"""API ewidencji jednostek wojskowych – S22IT1S0P2-7."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..models import Jednostka
from .zaleznosci import get_sesja, uzytkownik_sesji

router = APIRouter(
    prefix="/jednostki",
    tags=["Jednostki"],
    dependencies=[Depends(uzytkownik_sesji)],
)


class JednostkaIn(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    id_jednostki: str = Field(
        min_length=1,
        max_length=20,
        pattern=r"^\S+$",
    )
    nazwa: str = Field(
        min_length=1,
        max_length=200,
    )
    miejscowosc: str = Field(
        min_length=1,
        max_length=100,
    )


class JednostkaOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id_jednostki: str
    nazwa: str
    miejscowosc: str


@router.post("", response_model=JednostkaOut, status_code=201)
def zarejestruj_jednostke(
    dane: JednostkaIn,
    s: Session = Depends(get_sesja),
):
    if s.get(Jednostka, dane.id_jednostki) is not None:
        raise HTTPException(
            status_code=409,
            detail="Jednostka o podanym identyfikatorze już istnieje",
        )

    jednostka = Jednostka(**dane.model_dump())
    s.add(jednostka)

    try:
        s.flush()
    except IntegrityError as exc:
        s.rollback()
        raise HTTPException(
            status_code=409,
            detail="Konflikt podczas rejestracji jednostki",
        ) from exc

    return jednostka
