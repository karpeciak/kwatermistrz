"""Rozliczenie – S22IT1S0P2-36 (wyliczenie należności), -20 (duplikaty dokumentów), -43 (weryfikacja i przekazanie).

Do zrobienia w sprincie 1:
- S22IT1S0P2-36: `POST /delegacje/{id}/rozliczenie` – faktyczny czas podróży, zapewnione świadczenia, dokumenty
  kosztowe (delegacja w statusie `Status.DO_ROZLICZENIA`) → `Typ.ZADANIE_ROZLICZENIA`;
  `GET /delegacje/{id}/rozliczenie` – zestawienie składników i kwota należności;
  zatwierdzenie wyliczenia przez delegowanego.

Zrobione:
- S22IT1S0P2-43: `POST /delegacje/{id}/rozliczenie/przekazanie` → `Typ.ZADANIE_WERYFIKACJI`;
  `GET /delegacje/{id}/rozliczenie/weryfikacja` – wynik weryfikacji z korektami;
  `POST /delegacje/{id}/rozliczenie/akceptacja` – akceptacja albo zastrzeżenia → `Typ.AKCEPTACJA_ROZLICZENIA`.
- S22IT1S0P2-20: `POST /delegacje/{id}/rozliczenie/dokumenty` przyjmuje dokumenty, odrzuca duplikaty oraz loguje
  odrzucenie.
"""

from dataclasses import dataclass
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..agents.dokumenty_kosztowe import klucz_dokumentu, znajdz_duplikat
from ..audit import zapisz_operacje
from ..messaging import Broker, Komunikat, Typ
from ..models import (
    Delegacja,
    DokumentKosztowy,
    Jednostka,
    KorektaRozliczenia,
    Rozliczenie,
    Status,
    StatusRozliczenia,
    Zolnierz,
)
from .zaleznosci import get_broker, get_sesja, pobierz_delegacje
from .zatwierdzenia import koszt_delegacji

router = APIRouter(tags=["Rozliczenie"])

KOMUNIKAT_PRZYJETY = "Dokument kosztowy został przyjęty i przypisany do bieżącego rozliczenia."
KOMUNIKAT_DUPLIKAT = (
    "Dokument kosztowy został odrzucony, ponieważ dokument o takim numerze, dacie i kwocie jest już zarejestrowany."
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
    klucz = klucz_dokumentu(dane.numer, dane.data, kwota)
    if znajdz_duplikat(s, klucz) is not None:
        _zapisz_operacje_dokumentu(
            s,
            delegacja.id_delegacji,
            "Ostrzeżenie",
            KOMUNIKAT_LOG_DUPLIKAT,
        )
        return WynikPrzyjecia(False, KOMUNIKAT_DUPLIKAT)

    rozliczenie = s.scalar(select(Rozliczenie).where(Rozliczenie.id_delegacji == delegacja.id_delegacji))
    if not mozna_edytowac(rozliczenie):
        return WynikPrzyjecia(False, KOMUNIKAT_ZATWIERDZONE)
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


# ---------------------------------------------------------------- S22IT1S0P2-36: wyliczenie i zatwierdzenie

POLA_WYLICZENIA = ("dieta", "zwrot_przejazdu", "zwrot_noclegu", "ryczalty", "kwota_naleznosci")
KOMUNIKAT_ZATWIERDZONE = "Rozliczenie zostało już zatwierdzone – nie można go zmieniać."


def rozliczenie_delegacji(s: Session, d: Delegacja) -> Rozliczenie | None:
    return s.scalar(select(Rozliczenie).where(Rozliczenie.id_delegacji == d.id_delegacji))


def mozna_edytowac(rozliczenie: Rozliczenie | None) -> bool:
    """Dane rozliczenia zmienia się tylko przed zatwierdzeniem wyliczenia przez delegowanego."""
    return rozliczenie is None or rozliczenie.status == StatusRozliczenia.W_PRZYGOTOWANIU


def zapisz_podroz(
    s: Session,
    d: Delegacja,
    wyjazd: datetime,
    powrot: datetime,
    posilki: dict[str, int],
    bezplatne_zakwaterowanie: bool,
) -> Rozliczenie:
    """Faktyczny czas podróży i zapewnione świadczenia przekazane przez delegowanego."""
    rozliczenie = rozliczenie_delegacji(s, d)
    if rozliczenie is None:
        rozliczenie = Rozliczenie(id_delegacji=d.id_delegacji)
        s.add(rozliczenie)
    rozliczenie.faktyczny_wyjazd = wyjazd
    rozliczenie.faktyczny_powrot = powrot
    rozliczenie.zapewnione_posilki = {posilek: liczba for posilek, liczba in posilki.items() if liczba}
    rozliczenie.bezplatne_zakwaterowanie = bezplatne_zakwaterowanie
    s.flush()
    return rozliczenie


async def zlec_wyliczenie(s: Session, broker: Broker, d: Delegacja) -> None:
    """Unieważnia poprzednie wyliczenie i zleca nowe Agentowi Rozliczeń, gdy znany jest czas podróży."""
    rozliczenie = rozliczenie_delegacji(s, d)
    if rozliczenie is None:
        return
    for pole in POLA_WYLICZENIA:
        setattr(rozliczenie, pole, None)
    s.commit()
    if rozliczenie.faktyczny_wyjazd and rozliczenie.faktyczny_powrot:
        await broker.publikuj(Komunikat(typ=Typ.ZADANIE_ROZLICZENIA, id_delegacji=d.id_delegacji))


async def zatwierdz_wyliczenie(s: Session, broker: Broker, d: Delegacja) -> str | None:
    """Delegowany zatwierdza wyliczenie → gotowe do weryfikacji; zwraca opis błędu albo None.

    S22IT1S0P2-66: szacowany koszt przestaje być zaangażowany w budżecie jednostki, a należność staje się wydatkiem.
    """
    rozliczenie = rozliczenie_delegacji(s, d)
    if rozliczenie is None or rozliczenie.kwota_naleznosci is None:
        return "Nie ma jeszcze wyliczenia należności do zatwierdzenia."
    if rozliczenie.status != StatusRozliczenia.W_PRZYGOTOWANIU:
        return "Wyliczenie zostało już zatwierdzone."
    rozliczenie.status = StatusRozliczenia.GOTOWE_DO_WERYFIKACJI
    kwota = rozliczenie.kwota_naleznosci
    opis = f"Delegowany zatwierdził wyliczenie: {kwota:.2f} zł – gotowe do weryfikacji"

    wnioskodawca = s.get(Zolnierz, d.id_zolnierza)
    jednostka = s.get(Jednostka, wnioskodawca.id_jednostki) if wnioskodawca else None
    if jednostka is not None:
        zaangazowane = koszt_delegacji(d) or Decimal("0")
        jednostka.budzet_wykorzystany = max(Decimal("0"), (jednostka.budzet_wykorzystany or 0) - zaangazowane)
        jednostka.budzet_wydatkowany = (jednostka.budzet_wydatkowany or 0) + kwota
        opis += f"; budżet: zaangażowanie −{zaangazowane:.2f} zł, wydatek +{kwota:.2f} zł"

    zapisz_operacje(
        s,
        id_korelacji=f"COR-{d.id_delegacji}",
        id_delegacji=d.id_delegacji,
        id_agenta="AG-06",
        etap=Status.DO_ROZLICZENIA,
        operacja="Zatwierdzenie wyliczenia przez delegowanego",
        wynik="Sukces",
        czas_trwania_ms=0,
        nr_proby=1,
        komunikat=opis,
        id_komunikatu=None,
    )
    s.commit()
    await broker.publikuj(
        Komunikat(typ=Typ.WYNIK_ROZLICZENIA, id_delegacji=d.id_delegacji, dane={"kwota_naleznosci": str(kwota)})
    )
    return None


class PodrozIn(BaseModel):
    faktyczny_wyjazd: datetime
    faktyczny_powrot: datetime
    zapewnione_posilki: dict[Literal["śniadanie", "obiad", "kolacja"], int] = Field(default_factory=dict)
    bezplatne_zakwaterowanie: bool = False

    @model_validator(mode="after")
    def sprawdz(self) -> "PodrozIn":
        if self.faktyczny_powrot <= self.faktyczny_wyjazd:
            raise ValueError("powrót musi nastąpić po wyjeździe")
        if any(liczba < 0 for liczba in self.zapewnione_posilki.values()):
            raise ValueError("liczba posiłków nie może być ujemna")
        return self


def delegacja_do_rozliczenia(s: Session, id_delegacji: str) -> Delegacja:
    d = pobierz_delegacje(s, id_delegacji)
    if d.status != Status.DO_ROZLICZENIA:
        raise HTTPException(409, "Delegacja nie kwalifikuje się do rozliczenia")
    return d


@router.post("/delegacje/{id_delegacji}/rozliczenie", status_code=202)
async def przekaz_dane_rozliczenia(
    id_delegacji: str,
    dane: PodrozIn,
    s: Session = Depends(get_sesja),
    broker: Broker = Depends(get_broker),
):
    """Faktyczny czas podróży i zapewnione świadczenia → wyliczenie należności przez Agenta Rozliczeń."""
    d = delegacja_do_rozliczenia(s, id_delegacji)
    if not mozna_edytowac(rozliczenie_delegacji(s, d)):
        raise HTTPException(409, KOMUNIKAT_ZATWIERDZONE)
    posilki = dict(dane.zapewnione_posilki)
    zapisz_podroz(s, d, dane.faktyczny_wyjazd, dane.faktyczny_powrot, posilki, dane.bezplatne_zakwaterowanie)
    await zlec_wyliczenie(s, broker, d)
    return {"przyjeto": True}


@router.get("/delegacje/{id_delegacji}/rozliczenie")
def zestawienie_rozliczenia(id_delegacji: str, s: Session = Depends(get_sesja)):
    """Zestawienie składników rozliczenia z kwotą należności i oznaczeniem przekroczeń limitu."""
    d = pobierz_delegacje(s, id_delegacji)
    rozliczenie = rozliczenie_delegacji(s, d)
    if rozliczenie is None:
        raise HTTPException(404, "Ta delegacja nie ma jeszcze rozliczenia")
    dokumenty = s.scalars(
        select(DokumentKosztowy)
        .where(DokumentKosztowy.id_rozliczenia == rozliczenie.id_rozliczenia)
        .order_by(DokumentKosztowy.id_dokumentu)
    ).all()
    return {
        "id_delegacji": d.id_delegacji,
        "status": rozliczenie.status,
        "faktyczny_wyjazd": rozliczenie.faktyczny_wyjazd,
        "faktyczny_powrot": rozliczenie.faktyczny_powrot,
        "zapewnione_posilki": rozliczenie.zapewnione_posilki or {},
        "bezplatne_zakwaterowanie": rozliczenie.bezplatne_zakwaterowanie,
        "dokumenty": [
            {
                "rodzaj": dok.rodzaj,
                "numer": dok.numer,
                "data": dok.data,
                "kwota": dok.kwota,
                "kwota_uznana": dok.kwota_uznana,
                "przekracza_limit": dok.przekracza_limit,
            }
            for dok in dokumenty
        ],
        "wyliczenie": None
        if rozliczenie.kwota_naleznosci is None
        else {pole: getattr(rozliczenie, pole) for pole in POLA_WYLICZENIA},
    }


@router.post("/delegacje/{id_delegacji}/rozliczenie/zatwierdzenie")
async def zatwierdz_wyliczenie_api(
    id_delegacji: str,
    s: Session = Depends(get_sesja),
    broker: Broker = Depends(get_broker),
):
    """Delegowany zatwierdza wyliczenie – rozliczenie trafia do weryfikacji (S22IT1S0P2-43)."""
    d = delegacja_do_rozliczenia(s, id_delegacji)
    komunikat = await zatwierdz_wyliczenie(s, broker, d)
    if komunikat:
        raise HTTPException(409, komunikat)
    return {"status": StatusRozliczenia.GOTOWE_DO_WERYFIKACJI}


# ---------------------------------------------------------------- S22IT1S0P2-43: weryfikacja i przekazanie


class AkceptacjaIn(BaseModel):
    """Brak zastrzeżeń = akceptacja zweryfikowanego wyliczenia (scenariusz główny); zastrzeżenia – 3a–4a."""

    zastrzezenia: str | None = Field(None, max_length=1000)


KOMUNIKAT_NIE_ZATWIERDZONO = "Najpierw zatwierdź wyliczenie należności."
KOMUNIKAT_NIE_ZWERYFIKOWANO = "Rozliczenie nie jest jeszcze zweryfikowane."


def korekty_rozliczenia(s: Session, rozliczenie: Rozliczenie) -> list[KorektaRozliczenia]:
    return list(
        s.scalars(
            select(KorektaRozliczenia)
            .where(KorektaRozliczenia.id_rozliczenia == rozliczenie.id_rozliczenia)
            .order_by(KorektaRozliczenia.id_korekty)
        ).all()
    )


async def zlec_weryfikacje(s: Session, broker: Broker, d: Delegacja) -> str | None:
    """Zatwierdzone wyliczenie idzie do Agenta Zgodności; zwraca opis błędu albo None (wspólne dla API i `/app`)."""
    rozliczenie = rozliczenie_delegacji(s, d)
    if rozliczenie is None or rozliczenie.status != StatusRozliczenia.GOTOWE_DO_WERYFIKACJI:
        return KOMUNIKAT_NIE_ZATWIERDZONO
    await broker.publikuj(Komunikat(typ=Typ.ZADANIE_WERYFIKACJI, id_delegacji=d.id_delegacji))
    return None


async def przekaz_rozliczenie(s: Session, broker: Broker, d: Delegacja, zastrzezenia: str | None) -> str | None:
    """Akceptacja albo zastrzeżenia delegowanego → przekazanie do finansów; zwraca opis błędu albo None."""
    rozliczenie = rozliczenie_delegacji(s, d)
    if rozliczenie is None or rozliczenie.status != StatusRozliczenia.ZWERYFIKOWANE:
        return KOMUNIKAT_NIE_ZWERYFIKOWANO
    dane = {"zastrzezenia": (zastrzezenia or "").strip() or None}
    await broker.publikuj(Komunikat(typ=Typ.AKCEPTACJA_ROZLICZENIA, id_delegacji=d.id_delegacji, dane=dane))
    return None


@router.post("/delegacje/{id_delegacji}/rozliczenie/przekazanie", status_code=202)
async def zlec_przekazanie(
    id_delegacji: str,
    s: Session = Depends(get_sesja),
    broker: Broker = Depends(get_broker),
):
    """Delegowany zleca przekazanie zatwierdzonego wyliczenia – Agent Zgodności je weryfikuje."""
    komunikat = await zlec_weryfikacje(s, broker, pobierz_delegacje(s, id_delegacji))
    if komunikat:
        raise HTTPException(409, komunikat)
    return {"przyjeto": True}


@router.get("/delegacje/{id_delegacji}/rozliczenie/weryfikacja")
def wynik_weryfikacji(id_delegacji: str, s: Session = Depends(get_sesja)):
    """Stan weryfikacji i przekazania z korektami wprowadzonymi przez Agenta Zgodności."""
    d = pobierz_delegacje(s, id_delegacji)
    rozliczenie = rozliczenie_delegacji(s, d)
    if rozliczenie is None:
        raise HTTPException(404, "Ta delegacja nie ma jeszcze rozliczenia")
    return {
        "id_delegacji": d.id_delegacji,
        "status": rozliczenie.status,
        "kwota_naleznosci": rozliczenie.kwota_naleznosci,
        "korekty": [
            {"pozycja": k.pozycja, "przed": k.wartosc_przed, "po": k.wartosc_po, "powod": k.powod}
            for k in korekty_rozliczenia(s, rozliczenie)
        ],
        "zastrzezenia": rozliczenie.zastrzezenia,
        "data_przekazania": rozliczenie.data_przekazania,
    }


@router.post("/delegacje/{id_delegacji}/rozliczenie/akceptacja", status_code=202)
async def akceptuj_rozliczenie(
    id_delegacji: str,
    dane: AkceptacjaIn,
    s: Session = Depends(get_sesja),
    broker: Broker = Depends(get_broker),
):
    """Delegowany akceptuje zweryfikowane wyliczenie albo zgłasza zastrzeżenia – rozliczenie idzie do finansów."""
    komunikat = await przekaz_rozliczenie(s, broker, pobierz_delegacje(s, id_delegacji), dane.zastrzezenia)
    if komunikat:
        raise HTTPException(409, komunikat)
    return {"przyjeto": True}
