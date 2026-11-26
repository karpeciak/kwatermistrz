import pytest
from fastapi.testclient import TestClient
from pomoc_sesji import zaloguj_z_csrf

from kwatermistrz.api.main import app
from kwatermistrz.db import sesja
from kwatermistrz.models import Jednostka, Zolnierz
from kwatermistrz.seed import zaladuj_dane_poczatkowe


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


def test_lista_jednostek_zawiera_nowa_jednostke():
    dane = {
        "id_jednostki": "JW-2099",
        "nazwa": "Jednostka Testowa",
        "miejscowosc": "Kraków",
    }

    with TestClient(app) as klient:
        klient.post(
            "/app/logowanie",
            data={"id_zolnierza": "ZOL-001"},
            follow_redirects=False,
        )

        zapis = klient.post("/jednostki", json=dane)
        assert zapis.status_code == 201

        odpowiedz = klient.get("/jednostki")
        assert odpowiedz.status_code == 200

        jednostki = odpowiedz.json()

        assert any(j["id_jednostki"] == "JW-2099" and j["nazwa"] == "Jednostka Testowa" for j in jednostki)


def test_jednostka_pozostaje_po_zaladowaniu_danych_poczatkowych():
    with sesja() as s:
        s.add(
            Jednostka(
                id_jednostki="JW-9090",
                nazwa="Jednostka Integracyjna",
                miejscowosc="Kraków",
            )
        )

    with sesja() as s:
        zaladuj_dane_poczatkowe(s)

    with sesja() as s:
        jednostka = s.get(Jednostka, "JW-9090")

        assert jednostka is not None
        assert jednostka.nazwa == "Jednostka Integracyjna"
        assert jednostka.miejscowosc == "Kraków"


def test_przypisanie_zolnierza_do_nowej_jednostki():
    with sesja() as s:
        s.add(
            Jednostka(
                id_jednostki="JW-9091",
                nazwa="Jednostka Testowa",
                miejscowosc="Warszawa",
            )
        )
        s.flush()

        s.add(
            Zolnierz(
                id_zolnierza="ZOL-9091",
                stopien="kpr.",
                imie_nazwisko="Jan Testowy",
                id_jednostki="JW-9091",
            )
        )

    with sesja() as s:
        zolnierz = s.get(Zolnierz, "ZOL-9091")
        jednostka = s.get(Jednostka, "JW-9091")

        assert zolnierz is not None
        assert jednostka is not None
        assert zolnierz.id_jednostki == jednostka.id_jednostki


def test_strona_jednostek_wymaga_logowania():
    with TestClient(app, follow_redirects=False) as klient:
        odpowiedz = klient.get("/app/jednostki")

        assert odpowiedz.status_code == 303
        assert odpowiedz.headers["location"] == "/app/logowanie"


def test_formularz_rejestruje_nowa_jednostke():
    with TestClient(app, follow_redirects=False) as klient:
        zaloguj_z_csrf(klient, "ZOL-001")

        strona = klient.get("/app/jednostki")
        assert strona.status_code == 200
        assert "Ewidencja jednostek wojskowych" in strona.text

        dane = {
            "id_jednostki": "JW-8881",
            "nazwa": "Jednostka Formularza",
            "miejscowosc": "Kielce",
            "csrf": klient.headers["X-CSRF-Token"],
        }

        odpowiedz = klient.post("/app/jednostki", data=dane)

        assert odpowiedz.status_code == 303
        assert odpowiedz.headers["location"] == "/app/jednostki?dodano=true"

        lista = klient.get("/app/jednostki")
        assert "JW-8881" in lista.text
        assert "Jednostka Formularza" in lista.text

    with sesja() as s:
        assert s.get(Jednostka, "JW-8881") is not None


def test_formularz_obsluguje_duplikaty_i_bledne_dane():
    with TestClient(app, follow_redirects=False) as klient:
        zaloguj_z_csrf(klient, "ZOL-001")

        dane = {
            "id_jednostki": "JW-8882",
            "nazwa": "Jednostka Testowa",
            "miejscowosc": "Kraków",
            "csrf": klient.headers["X-CSRF-Token"],
        }

        pierwszy = klient.post("/app/jednostki", data=dane)
        assert pierwszy.status_code == 303

        duplikat = klient.post("/app/jednostki", data=dane)
        assert duplikat.status_code == 409
        assert "już istnieje" in duplikat.text

        niepoprawne = {
            **dane,
            "id_jednostki": "JW-8883",
            "nazwa": "   ",
        }

        blad = klient.post("/app/jednostki", data=niepoprawne)
        assert blad.status_code == 422

    with sesja() as s:
        assert s.get(Jednostka, "JW-8883") is None


def test_formularz_blokuje_brak_csrf():
    with TestClient(app, follow_redirects=False) as klient:
        klient.post(
            "/app/logowanie",
            data={"id_zolnierza": "ZOL-001"},
        )

        dane = {
            "id_jednostki": "JW-8884",
            "nazwa": "Jednostka Bez CSRF",
            "miejscowosc": "Warszawa",
        }

        odpowiedz = klient.post("/app/jednostki", data=dane)
        assert odpowiedz.status_code == 403

        niepoprawny_token = klient.post(
            "/app/jednostki",
            data={**dane, "csrf": "niepoprawny"},
        )
        assert niepoprawny_token.status_code == 403

    with sesja() as s:
        assert s.get(Jednostka, "JW-8884") is None
