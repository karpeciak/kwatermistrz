r"""Lokalny podgląd S22IT1S0P2-27 bez PostgreSQL i RabbitMQ.

Uruchom: .venv\Scripts\python podglad_transportu.py
Każde uruchomienie tworzy osobną, tymczasową bazę z dwiema delegacjami demo.
Komunikaty transportu są dostarczane w pamięci do rzeczywistego agenta.
"""

import os
import tempfile
from contextlib import asynccontextmanager
from datetime import date, time
from pathlib import Path

BAZA_DEMO = Path(tempfile.mkdtemp(prefix="kwatermistrz-podglad-")) / "podglad.db"
os.environ["DATABASE_URL"] = f"sqlite:///{BAZA_DEMO.as_posix()}"
os.environ["POLACZ_BROKER"] = "false"

import uvicorn  # noqa: E402
from fastapi import HTTPException  # noqa: E402

from kwatermistrz.agents.transport import AgentTransportu  # noqa: E402
from kwatermistrz.api.main import app  # noqa: E402
from kwatermistrz.db import sesja, teraz  # noqa: E402
from kwatermistrz.messaging import Komunikat, Typ  # noqa: E402
from kwatermistrz.models import Delegacja, Przedsiewziecie, Status  # noqa: E402

DELEGACJE = ("DEMO-TRANSPORT", "DEMO-ODRZUCENIE")


class BrokerPodgladu:
    """Atrapa transportu wiadomości wyłącznie do lokalnego podglądu."""

    def __init__(self):
        self.agent = AgentTransportu(self)

    async def publikuj(self, k: Komunikat):
        obsluga = self.agent.obslugi().get(k.typ)
        if obsluga:
            wynik = await self.agent.wykonaj(k, *obsluga)
            if wynik.wynik == "Błąd":
                raise HTTPException(409, wynik.opis)
        elif k.typ not in (Typ.POWIADOMIENIE, Typ.WYNIK_TRANSPORTU):
            raise HTTPException(503, "Podgląd obejmuje wybór podróży. Pozostałe procesy wymagają pełnego systemu.")

    async def do_kolejki(self, kolejka: str, k: Komunikat):
        raise RuntimeError("Błąd obsługi podglądu — sprawdź log w terminalu")


def utworz_dane_demo():
    with sesja() as s:
        s.add(
            Przedsiewziecie(
                id_przedsiewziecia="PRZ-PODGLAD",
                nazwa="Szkolenie demonstracyjne transportu",
                miejscowosc="Toruń",
                data_rozpoczecia=date(2026, 12, 7),
                data_zakonczenia=date(2026, 12, 11),
                godzina_rozpoczecia=time(9),
            )
        )
        s.flush()
        for identyfikator in DELEGACJE:
            s.add(
                Delegacja(
                    id_delegacji=identyfikator,
                    id_zolnierza="ZOL-003",
                    id_zatwierdzajacego="ZOL-001",
                    id_przedsiewziecia="PRZ-PODGLAD",
                    status=Status.ZATWIERDZONY,
                    cel_wyjazdu="Podgląd porównania i wyboru podróży",
                    miejsce_docelowe="Toruń",
                    data_wyjazdu=date(2026, 12, 7),
                    data_powrotu=date(2026, 12, 11),
                    data_zlozenia=teraz(),
                    srodek_transportu="kolej",
                    potrzeba_zakwaterowania=False,
                    dane_wniosku={
                        "cel_wyjazdu": "Podgląd porównania i wyboru podróży",
                        "miejsce_docelowe": "Toruń",
                        "data_wyjazdu": "2026-12-07",
                        "data_powrotu": "2026-12-11",
                        "srodek_transportu": "kolej",
                    },
                )
            )


zwykly_start = app.router.lifespan_context


@asynccontextmanager
async def start_podgladu(aplikacja):
    async with zwykly_start(aplikacja):
        utworz_dane_demo()
        broker = BrokerPodgladu()
        aplikacja.state.broker = broker
        for identyfikator in DELEGACJE:
            await broker.publikuj(Komunikat(typ=Typ.ZADANIE_TRANSPORTU, id_delegacji=identyfikator))
        print("\nZaloguj się jako ZOL-001: http://127.0.0.1:8000/app/logowanie")
        for identyfikator in DELEGACJE:
            print(f"Podgląd: http://127.0.0.1:8000/app/wnioski/{identyfikator}/podroz")
        print("Osobna baza tymczasowa; tylko transport. Restart przywraca świeże dane. Ctrl+C kończy podgląd.\n")
        yield


app.router.lifespan_context = start_podgladu


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8000)
