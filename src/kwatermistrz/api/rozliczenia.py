"""Rozliczenie – S22IT1S0P2-36 (wyliczenie należności), -20 (duplikaty dokumentów), -43 (weryfikacja i przekazanie).

Do zrobienia w sprincie 1:
- S22IT1S0P2-36: `POST /delegacje/{id}/rozliczenie` – faktyczny czas podróży, zapewnione świadczenia, dokumenty
  kosztowe (delegacja w statusie `Status.DO_ROZLICZENIA`) → `Typ.ZADANIE_ROZLICZENIA`;
  `GET /delegacje/{id}/rozliczenie` – zestawienie składników i kwota należności;
  zatwierdzenie wyliczenia przez delegowanego.
- S22IT1S0P2-20: przy przyjmowaniu dokumentów – `agents/dokumenty_kosztowe.py` (klucz i wykrywanie duplikatu),
  odrzucenie duplikatu z czytelną przyczyną.
- S22IT1S0P2-43: `POST /delegacje/{id}/rozliczenie/przekazanie` → `Typ.ZADANIE_WERYFIKACJI`;
  akceptacja zweryfikowanego wyliczenia albo zastrzeżenia → `Typ.AKCEPTACJA_ROZLICZENIA`.
"""

from fastapi import APIRouter

router = APIRouter(tags=["Rozliczenie"])
