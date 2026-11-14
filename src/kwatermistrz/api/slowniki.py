"""Słowniki formularza; typ transportu nie oznacza dostępnego pojazdu lub biletu."""

from fastapi import APIRouter, Depends, HTTPException, Query

from ..miejscowosci import BladMiejscowosci, ZrodloNiedostepne, szukaj_miejscowosci
from ..podroze import SRODKI_TRANSPORTU
from .zaleznosci import uzytkownik_sesji

router = APIRouter(prefix="/slowniki", tags=["Słowniki"], dependencies=[Depends(uzytkownik_sesji)])


@router.get("/miejscowosci")
def miejscowosci(q: str = Query(min_length=2, max_length=80)):
    try:
        return {"wyniki": szukaj_miejscowosci(q), "zrodlo": "Photon / © OpenStreetMap contributors"}
    except BladMiejscowosci as e:
        raise HTTPException(422, str(e)) from e
    except ZrodloNiedostepne as e:
        raise HTTPException(503, str(e)) from e


@router.get("/srodki-transportu")
def srodki_transportu():
    return [{"wartosc": k, "nazwa": v} for k, v in SRODKI_TRANSPORTU.items()]
