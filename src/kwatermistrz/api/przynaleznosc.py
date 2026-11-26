"""API historii przynaleznosci organizacyjnej zolnierza."""

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Jednostka, Zolnierz
from ..przynaleznosc import dostep_do_przynaleznosci, historia_przynaleznosci, zmien_jednostke
from .schemas import PrzynaleznoscOut, ZmianaPrzynaleznosciIn
from .zaleznosci import get_sesja, sprawdz_csrf, uzytkownik_sesji

router = APIRouter(tags=["Przynaleznosc"])


def odpowiedz_przynaleznosci(s: Session, id_zolnierza: str) -> PrzynaleznoscOut:
    zolnierz = s.get(Zolnierz, id_zolnierza)
    if zolnierz is None:
        raise HTTPException(404, f"Nie znaleziono żołnierza {id_zolnierza}")
    jednostka = s.get(Jednostka, zolnierz.id_jednostki)
    if jednostka is None:
        raise HTTPException(409, "Bieżąca jednostka żołnierza nie istnieje")
    wpisy = historia_przynaleznosci(s, id_zolnierza)
    jednostki = {
        j.id_jednostki: j
        for j in s.scalars(select(Jednostka).where(Jednostka.id_jednostki.in_({w.id_jednostki for w in wpisy})))
    }
    return PrzynaleznoscOut(
        id_zolnierza=id_zolnierza,
        aktualna_jednostka={
            "id_jednostki": jednostka.id_jednostki,
            "nazwa": jednostka.nazwa,
            "miejscowosc": jednostka.miejscowosc,
        },
        historia=[
            {
                "id_przynaleznosci": wpis.id_przynaleznosci,
                "id_jednostki": wpis.id_jednostki,
                "nazwa_jednostki": jednostki[wpis.id_jednostki].nazwa,
                "data_od": wpis.data_od,
                "data_do": wpis.data_do,
                "powod": wpis.powod,
                "id_operatora": wpis.id_operatora,
                "zrodlo": wpis.zrodlo,
            }
            for wpis in wpisy
        ],
    )


@router.get("/zolnierze/{id_zolnierza}/przynaleznosc", response_model=PrzynaleznoscOut)
def pokaz_przynaleznosc(
    id_zolnierza: str,
    s: Session = Depends(get_sesja),
    uzytkownik: Zolnierz = Depends(uzytkownik_sesji),
):
    dostep_do_przynaleznosci(s, id_zolnierza, uzytkownik)
    return odpowiedz_przynaleznosci(s, id_zolnierza)


@router.post("/zolnierze/{id_zolnierza}/przynaleznosc", response_model=PrzynaleznoscOut)
def zmien_przynaleznosc(
    id_zolnierza: str,
    dane: ZmianaPrzynaleznosciIn,
    request: Request,
    token_csrf: str | None = Header(None, alias="X-CSRF-Token"),
    s: Session = Depends(get_sesja),
    uzytkownik: Zolnierz = Depends(uzytkownik_sesji),
):
    sprawdz_csrf(request, token_csrf)
    zmien_jednostke(
        s,
        id_zolnierza,
        dane.id_jednostki,
        dane.data_od,
        operator=uzytkownik,
        powod=dane.powod,
        zrodlo="api",
    )
    s.commit()
    return odpowiedz_przynaleznosci(s, id_zolnierza)
