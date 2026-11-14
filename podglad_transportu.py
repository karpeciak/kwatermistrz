r"""Lokalne uruchomienie aplikacji ze wszystkimi agentami z repozytorium.

Uruchom: .venv\Scripts\python podglad_transportu.py
Dane są zachowywane w osobnej bazie SQLite podglad-lokalny.db.
Wiadomości wszystkich agentów są dostarczane w pamięci. Można tworzyć własne wnioski.
"""

import asyncio
import os
import subprocess
import sys
from contextlib import asynccontextmanager
from datetime import date, time
from pathlib import Path

BAZA_DEMO = Path(__file__).resolve().parent / "podglad-lokalny.db"
os.environ["DATABASE_URL"] = f"sqlite:///{BAZA_DEMO.as_posix()}"
os.environ["POLACZ_BROKER"] = "false"

import uvicorn  # noqa: E402

from kwatermistrz.api.main import app  # noqa: E402
from kwatermistrz.db import sesja, teraz  # noqa: E402
from kwatermistrz.lokalnie import BrokerLokalny  # noqa: E402
from kwatermistrz.messaging import Komunikat, Typ  # noqa: E402
from kwatermistrz.models import Delegacja, Przedsiewziecie, Status  # noqa: E402

DELEGACJE = ("DEMO-TRANSPORT", "DEMO-ODRZUCENIE")


def utworz_dane_demo():
    with sesja() as s:
        if s.get(Przedsiewziecie, "PRZ-PODGLAD") is None:
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
            if s.get(Delegacja, identyfikator) is not None:
                continue
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
        broker = BrokerLokalny()
        aplikacja.state.broker = broker
        for identyfikator in DELEGACJE:
            await broker.publikuj(Komunikat(typ=Typ.ZADANIE_TRANSPORTU, id_delegacji=identyfikator))
        zadania = [asyncio.create_task(a.petla_cykliczna()) for a in broker.agenci if a.interwal_cyklu]
        print("\nAplikacja: http://127.0.0.1:8000/app/logowanie")
        print("Wnioskodawca: ZOL-003. Przełożony: ZOL-001. Lista podróży: /app/podroze")
        print("Możesz tworzyć własne wnioski. Dostępne jest też szkolenie PRZ-PODGLAD (9:00).")
        print("Wszyscy agenci z repozytorium; SQLite i wiadomości w pamięci. Ctrl+C kończy serwer.\n")
        try:
            yield
        finally:
            for zadanie in zadania:
                zadanie.cancel()
            await asyncio.gather(*zadania, return_exceptions=True)


app.router.lifespan_context = start_podgladu


if __name__ == "__main__":
    print("Sprawdzam testy przed uruchomieniem aplikacji...", flush=True)
    try:
        kontrola = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", "--tb=short"],
            cwd=Path(__file__).resolve().parent,
            timeout=180,
            check=False,
        )
    except subprocess.TimeoutExpired:
        sys.exit("Testy nie zakończyły się w 180 sekund. Serwer nie został uruchomiony.")
    if kontrola.returncode:
        sys.exit("Testy wykazały błąd. Serwer nie został uruchomiony — wynik znajduje się powyżej.")
    uvicorn.run(app, host="127.0.0.1", port=8000)
