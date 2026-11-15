"""Organizacja wyjazdu – S22IT1S0P2-38 (polecenie wyjazdu), -27 (wybór wariantu podróży), -39 (zakwaterowanie).

Zrobione:
- S22IT1S0P2-38: `GET /delegacje/{id}/polecenie` – polecenie wyjazdu dla delegowanego (`PolecenieWyjazdu`).

Do zrobienia w sprincie 1:
- S22IT1S0P2-27: `GET /delegacje/{id}/warianty-podrozy` – propozycje z rekomendacją (`Przejazd`, status propozycja),
  `POST /delegacje/{id}/wybor-podrozy` – wybór wariantu z przyczyną albo odrzucenie wszystkich
  → komunikat `Typ.WYBOR_TRANSPORTU`.
- S22IT1S0P2-39: `GET /delegacje/{id}/zakwaterowanie` – propozycje obiektów (koszt, czas dojazdu),
  `POST /delegacje/{id}/wybor-zakwaterowania` – wybór obiektu (z uzasadnieniem przy odstępstwie)
  → komunikat `Typ.WYBOR_ZAKWATEROWANIA`.
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import PolecenieWyjazdu
from .zaleznosci import get_sesja, pobierz_delegacje

router = APIRouter(tags=["Organizacja wyjazdu"])


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
