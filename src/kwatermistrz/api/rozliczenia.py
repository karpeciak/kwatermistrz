"""Rozliczenie – S22IT1S0P2-36 (wyliczenie należności), -20 (duplikaty dokumentów), -43 (weryfikacja i przekazanie).

Do zrobienia w sprincie 1:
- S22IT1S0P2-36: `POST /delegacje/{id}/rozliczenie` – faktyczny czas podróży, zapewnione świadczenia, dokumenty
  kosztowe (delegacja w statusie `Status.DO_ROZLICZENIA`) → `Typ.ZADANIE_ROZLICZENIA`;
  `GET /delegacje/{id}/rozliczenie` – zestawienie składników i kwota należności;
  zatwierdzenie wyliczenia przez delegowanego.
- S22IT1S0P2-43: `POST /delegacje/{id}/rozliczenie/przekazanie` → `Typ.ZADANIE_WERYFIKACJI`;
  akceptacja zweryfikowanego wyliczenia albo zastrzeżenia → `Typ.AKCEPTACJA_ROZLICZENIA`.

Zrobione:
- S22IT1S0P2-20: `POST /delegacje/{id}/rozliczenie/dokumenty` przyjmuje dokumenty, odrzuca duplikaty oraz loguje
  odrzucenie.
"""

from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..agents.dokumenty_kosztowe import klucz_dokumentu, znajdz_duplikat
from ..audit import zapisz_operacje
from ..models import Delegacja, DokumentKosztowy, Rozliczenie, Status
from .zaleznosci import get_sesja, pobierz_delegacje

router = APIRouter(tags=["Rozliczenie"])

KOMUNIKAT_PRZYJETY = "Dokument kosztowy został przyjęty i przypisany do bieżącego rozliczenia."
KOMUNIKAT_DUPLIKAT = (
    "Dokument kosztowy został odrzucony, ponieważ dokument o takim rodzaju, numerze, dacie i kwocie "
    "jest już zarejestrowany."
)
KOMUNIKAT_LOG_DUPLIKAT = "Odrzucono duplikat dokumentu kosztowego; dokument jest już zarejestrowany."


class DokumentKosztowyIn(BaseModel):
    rodzaj: str = Field(min_length=1, max_length=30)
    numer: str = Field(min_length=1, max_length=60)
    data: date
    kwota: Decimal = Field(gt=0, le=Decimal("99999999.99"), max_digits=10)

    @field_validator("rodzaj", "numer")
    @classmethod
    def bez_zbednych_spacji(cls, wartosc: str) -> str:
        wartosc = " ".join(wartosc.split())
        if not wartosc:
            raise ValueError("pole nie może być puste")
        return wartosc


class DokumentKosztowyOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id_dokumentu: int
    id_rozliczenia: int
    rodzaj: str
    numer: str
    data: date
    kwota: Decimal


class WynikDokumentu(BaseModel):
    przyjeto: bool
    komunikat: str
    dokument: DokumentKosztowyOut | None = None


@dataclass
class WynikPrzyjecia:
    przyjeto: bool
    komunikat: str
    dokument: DokumentKosztowy | None = None


def _zapisz_operacje_dokumentu(s: Session, id_delegacji: str, wynik: str, komunikat: str) -> None:
    zapisz_operacje(
        s,
        id_korelacji=f"COR-{id_delegacji}",
        id_delegacji=id_delegacji,
        id_agenta="AG-06",
        etap=Status.DO_ROZLICZENIA,
        operacja="Dodanie dokumentu kosztowego",
        wynik=wynik,
        czas_trwania_ms=0,
        nr_proby=1,
        komunikat=komunikat,
        id_komunikatu=None,
    )


def przyjmij_dokument(s: Session, delegacja: Delegacja, dane: DokumentKosztowyIn) -> WynikPrzyjecia:
    """Przypisuje nowy dokument do rozliczenia albo bezpiecznie odrzuca globalny duplikat."""
    if delegacja.status != Status.DO_ROZLICZENIA:
        return WynikPrzyjecia(False, "Delegacja nie kwalifikuje się do rozliczenia.")

    kwota = dane.kwota.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    klucz = klucz_dokumentu(dane.rodzaj, dane.numer, dane.data, kwota)
    if znajdz_duplikat(s, klucz) is not None:
        _zapisz_operacje_dokumentu(
            s,
            delegacja.id_delegacji,
            "Ostrzeżenie",
            KOMUNIKAT_LOG_DUPLIKAT,
        )
        return WynikPrzyjecia(False, KOMUNIKAT_DUPLIKAT)

    rozliczenie = s.scalar(select(Rozliczenie).where(Rozliczenie.id_delegacji == delegacja.id_delegacji))
    if rozliczenie is None:
        rozliczenie = Rozliczenie(id_delegacji=delegacja.id_delegacji)
        try:
            with s.begin_nested():
                s.add(rozliczenie)
                s.flush()
        except IntegrityError:
            rozliczenie = s.scalar(select(Rozliczenie).where(Rozliczenie.id_delegacji == delegacja.id_delegacji))
            if rozliczenie is None:
                raise

    dokument = DokumentKosztowy(
        id_rozliczenia=rozliczenie.id_rozliczenia,
        rodzaj=dane.rodzaj,
        numer=dane.numer,
        data=dane.data,
        kwota=kwota,
        klucz_unikalnosci=klucz,
    )
    try:
        with s.begin_nested():
            s.add(dokument)
            s.flush()
    except IntegrityError:
        if znajdz_duplikat(s, klucz) is None:
            raise
        _zapisz_operacje_dokumentu(
            s,
            delegacja.id_delegacji,
            "Ostrzeżenie",
            KOMUNIKAT_LOG_DUPLIKAT,
        )
        return WynikPrzyjecia(False, KOMUNIKAT_DUPLIKAT)
    _zapisz_operacje_dokumentu(s, delegacja.id_delegacji, "Sukces", KOMUNIKAT_PRZYJETY)
    return WynikPrzyjecia(True, KOMUNIKAT_PRZYJETY, dokument)


@router.post(
    "/delegacje/{id_delegacji}/rozliczenie/dokumenty",
    response_model=WynikDokumentu,
    status_code=201,
    responses={409: {"model": WynikDokumentu}},
)
def dodaj_dokument_kosztowy(
    id_delegacji: str,
    dane: DokumentKosztowyIn,
    odpowiedz: Response,
    s: Session = Depends(get_sesja),
):
    delegacja = pobierz_delegacje(s, id_delegacji)
    wynik = przyjmij_dokument(s, delegacja, dane)
    if not wynik.przyjeto:
        odpowiedz.status_code = 409
    return WynikDokumentu(
        przyjeto=wynik.przyjeto,
        komunikat=wynik.komunikat,
        dokument=DokumentKosztowyOut.model_validate(wynik.dokument) if wynik.dokument else None,
    )
