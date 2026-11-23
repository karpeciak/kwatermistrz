import pytest
from fastapi.testclient import TestClient

from kwatermistrz.api.main import app


def test_rejestracja_jednostki():
    with TestClient(app) as klient:
        klient.post(
            "/app/logowanie",
            data={"id_zolnierza": "ZOL-001"},
            follow_redirects=False,
        )

        dane = {
            "id_jednostki": "JW-2002",
            "nazwa": "Jednostka Testowa",
            "miejscowosc": "Kraków",
        }

        wynik = klient.post("/jednostki", json=dane)

        assert wynik.status_code == 201
        assert wynik.json()["id_jednostki"] == "JW-2002"

        duplikat = klient.post("/jednostki", json=dane)
        assert duplikat.status_code == 409


def test_brak_sesji():
    with TestClient(app) as klient:
        wynik = klient.post(
            "/jednostki",
            json={
                "id_jednostki": "JW-3003",
                "nazwa": "Jednostka Testowa",
                "miejscowosc": "Warszawa",
            },
        )
        assert wynik.status_code == 401


@pytest.mark.parametrize(
    "zmiana",
    [
        {"id_jednostki": "   "},
        {"nazwa": "   "},
        {"miejscowosc": "   "},
        {"id_jednostki": "JW 2002"},
        {"id_jednostki": "J" * 21},
        {"nazwa": "N" * 201},
        {"miejscowosc": "M" * 101},
    ],
)
def test_walidacja_nieprawidlowych_danych(zmiana):
    dane = {
        "id_jednostki": "JW-2002",
        "nazwa": "Jednostka Testowa",
        "miejscowosc": "Kraków",
    }
    dane.update(zmiana)

    with TestClient(app) as klient:
        klient.post(
            "/app/logowanie",
            data={"id_zolnierza": "ZOL-001"},
            follow_redirects=False,
        )

        wynik = klient.post("/jednostki", json=dane)

        assert wynik.status_code == 422


@pytest.mark.parametrize(
    "pole",
    ["id_jednostki", "nazwa", "miejscowosc"],
)
def test_brak_wymaganego_pola(pole):
    dane = {
        "id_jednostki": "JW-2002",
        "nazwa": "Jednostka Testowa",
        "miejscowosc": "Kraków",
    }
    del dane[pole]

    with TestClient(app) as klient:
        klient.post(
            "/app/logowanie",
            data={"id_zolnierza": "ZOL-001"},
            follow_redirects=False,
        )

        wynik = klient.post("/jednostki", json=dane)

        assert wynik.status_code == 422


def test_usuwanie_zbednych_spacji():
    dane = {
        "id_jednostki": "  JW-2002  ",
        "nazwa": "  Jednostka Testowa  ",
        "miejscowosc": "  Kraków  ",
    }

    with TestClient(app) as klient:
        klient.post(
            "/app/logowanie",
            data={"id_zolnierza": "ZOL-001"},
            follow_redirects=False,
        )

        wynik = klient.post("/jednostki", json=dane)

        assert wynik.status_code == 201
        assert wynik.json()["id_jednostki"] == "JW-2002"
        assert wynik.json()["nazwa"] == "Jednostka Testowa"
        assert wynik.json()["miejscowosc"] == "Kraków"
