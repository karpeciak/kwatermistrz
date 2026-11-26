"""Ochrona zapisów formularzy: brak skutków ubocznych, token natywny i HTMX."""

import json
import re
from datetime import date
from html import unescape

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from kwatermistrz.api.main import app, get_broker
from kwatermistrz.config import settings
from kwatermistrz.db import sesja
from kwatermistrz.models import Delegacja, Rozliczenie, Status
from kwatermistrz.sesje import CIASTECZKO, podpis


@pytest.fixture
def klient(broker):
    app.dependency_overrides[get_broker] = lambda: broker
    with TestClient(app, follow_redirects=False) as c:
        c.post("/app/logowanie", data={"id_zolnierza": "ZOL-003"})
        yield c
    app.dependency_overrides.clear()


def token_z_html(tekst):
    wynik = re.search(r'name="csrf" value="([0-9a-f]{64})"', tekst)
    assert wynik, "Brak tokenu dla natywnego formularza"
    return wynik[1]


@pytest.mark.parametrize(
    "url",
    [
        "/app/wnioski/nowy",
        "/app/wnioski/nowy/podsumowanie",
        "/app/wnioski/nowy/roboczy",
        "/app/wnioski/nowy/roboczy/rezygnacja",
        "/app/do-zatwierdzenia",
        "/app/do-zatwierdzenia/DEL-CSRF",
        "/app/wnioski/DEL-CSRF/rozliczenie/podroz",
        "/app/wnioski/DEL-CSRF/rozliczenie/dokumenty",
        "/app/wnioski/DEL-CSRF/rozliczenie/zatwierdzenie",
        "/app/wnioski/DEL-CSRF/rozliczenie/przekazanie",
        "/app/wnioski/DEL-CSRF/rozliczenie/akceptacja",
        "/app/wnioski/DEL-CSRF/zakwaterowanie",
        "/app/zolnierze/ZOL-003/przynaleznosc",
    ],
)
def test_brak_tokena_blokuje_zapis_przed_skutkami_ubocznymi(klient, broker, url):
    with sesja() as s:
        s.add(Delegacja(id_delegacji="DEL-CSRF", id_zolnierza="ZOL-003", dane_wniosku={}, status=Status.DO_ROZLICZENIA))
    dane = {"decyzja": "Zgoda", "rodzaj": "bilet", "numer": "TEST-1", "data": "2026-12-07", "kwota": "10"}
    odpowiedz = klient.post(url, data=dane)
    assert odpowiedz.status_code == 403
    assert broker.wyslane == []
    with sesja() as s:
        assert s.scalar(select(func.count()).select_from(Delegacja)) == 1
        assert s.scalar(select(func.count()).select_from(Rozliczenie)) == 0


def test_natywny_formularz_i_autozapis_htmx_otrzymuja_token(klient):
    html = klient.get("/app/wnioski/nowy").text
    token = token_z_html(html)
    naglowki = json.loads(unescape(re.search(r"hx-headers='([^']+)'", html)[1]))
    assert naglowki == {"X-CSRF-Token": token}
    assert klient.post("/app/wnioski/nowy/roboczy", data={"cel_wyjazdu": "kurs", "csrf": token}).status_code == 200
    r = klient.post(
        "/app/wnioski/nowy/roboczy", data={"cel_wyjazdu": "poprawiony kurs"}, headers=naglowki | {"HX-Request": "true"}
    )
    assert r.status_code == 200
    with sesja() as s:
        robocza = s.scalar(select(Delegacja).where(Delegacja.status == Status.ROBOCZY))
        assert robocza.dane_wniosku["cel_wyjazdu"] == "poprawiony kurs"


def test_token_z_poprzedniego_logowania_nie_dziala(klient):
    token = token_z_html(klient.get("/app/wnioski/nowy").text)
    klient.post("/app/logowanie", data={"id_zolnierza": "ZOL-003"})
    r = klient.post("/app/wnioski/nowy/roboczy", data={"cel_wyjazdu": "kurs", "csrf": token})
    assert r.status_code == 403
    with sesja() as s:
        assert s.scalar(select(func.count()).select_from(Delegacja)) == 0


def test_token_natywnego_formularza_rozliczenia_nie_gubi_danych(klient):
    with sesja() as s:
        s.add(Delegacja(id_delegacji="DEL-CSRF", id_zolnierza="ZOL-003", dane_wniosku={}, status=Status.DO_ROZLICZENIA))
    html = klient.get("/app/wnioski/DEL-CSRF/rozliczenie").text
    token = token_z_html(html)
    r = klient.post(
        "/app/wnioski/DEL-CSRF/rozliczenie/podroz",
        data={
            "csrf": token,
            "faktyczny_wyjazd": "2026-12-07T06:00",
            "faktyczny_powrot": "2026-12-07T18:00",
            "sniadania": "1",
            "obiady": "0",
            "kolacje": "0",
        },
    )
    assert r.status_code == 303
    with sesja() as s:
        rozliczenie = s.scalar(select(Rozliczenie))
        assert rozliczenie.faktyczny_wyjazd.hour == 6 and rozliczenie.zapewnione_posilki == {"śniadanie": 1}


def test_fragment_htmx_po_bledzie_zachowuje_token(klient, broker):
    klient.post("/app/logowanie", data={"id_zolnierza": "ZOL-001"})
    with sesja() as s:
        s.add(
            Delegacja(
                id_delegacji="DEL-CSRF",
                id_zolnierza="ZOL-003",
                id_zatwierdzajacego="ZOL-001",
                dane_wniosku={},
                status=Status.W_AKCEPTACJI,
                data_wyjazdu=date(2026, 12, 7),
                data_powrotu=date(2026, 12, 11),
            )
        )
    token = token_z_html(klient.get("/app/do-zatwierdzenia").text)
    r = klient.post("/app/do-zatwierdzenia/DEL-CSRF", data={"csrf": token, "decyzja": "Odmowa"})
    assert r.status_code == 200 and "Podaj uzasadnienie" in r.text
    assert token_z_html(r.text) == token
    assert broker.wyslane == []
    r = klient.post("/app/do-zatwierdzenia/DEL-CSRF", data={"csrf": token, "decyzja": "Zgoda"})
    assert r.status_code == 200 and len(broker.wyslane) == 1


@pytest.mark.parametrize("token", ["nieprawidlowy", "nieprawidłowy"])
def test_nieprawidlowy_token_nie_powoduje_bledu_serwera(klient, token):
    r = klient.post("/app/wnioski/nowy/roboczy", data={"cel_wyjazdu": "kurs", "csrf": token})
    assert r.status_code == 403


def test_plik_zamiast_tokena_jest_odrzucany(klient):
    r = klient.post(
        "/app/wnioski/nowy/roboczy", data={"cel_wyjazdu": "kurs"}, files={"csrf": ("token.txt", b"token", "text/plain")}
    )
    assert r.status_code == 403


def test_wygasla_sesja_autozapisu_przekierowuje_cala_strone(klient):
    tresc = "ZOL-003.0.nonce"
    token = tresc + "." + podpis(tresc, settings.session_secret.get_secret_value())
    klient.cookies.clear()
    klient.cookies.set(CIASTECZKO, token)
    r = klient.post("/app/wnioski/nowy/roboczy", data={"cel_wyjazdu": "kurs"}, headers={"HX-Request": "true"})
    assert r.status_code == 401 and r.headers["HX-Redirect"] == "/app/logowanie"
    r = klient.get("/app/wnioski/nowy")
    assert r.status_code == 303 and r.headers["location"] == "/app/logowanie"
    with sesja() as s:
        assert s.scalar(select(func.count()).select_from(Delegacja)) == 0
