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
from pathlib import Path

BAZA_DEMO = Path(__file__).resolve().parent / "podglad-lokalny.db"
os.environ["DATABASE_URL"] = f"sqlite:///{BAZA_DEMO.as_posix()}"
os.environ["POLACZ_BROKER"] = "false"

import uvicorn  # noqa: E402

from kwatermistrz.api.main import app  # noqa: E402
from kwatermistrz.lokalnie import BrokerLokalny  # noqa: E402

zwykly_start = app.router.lifespan_context


@asynccontextmanager
async def start_podgladu(aplikacja):
    async with zwykly_start(aplikacja):
        broker = BrokerLokalny()
        aplikacja.state.broker = broker
        zadania = [asyncio.create_task(a.petla_cykliczna()) for a in broker.agenci if a.interwal_cyklu]
        print("\nAplikacja: http://127.0.0.1:8000/app/logowanie")
        print("Wnioskodawca: ZOL-003. Przełożony: ZOL-001. Lista podróży: /app/podroze")
        print("Możesz tworzyć własne wnioski. Serwer nie dodaje wniosków ani podróży demo.")
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
