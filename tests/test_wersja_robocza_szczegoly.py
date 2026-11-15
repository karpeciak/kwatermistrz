"""S22IT1S0P2-37, podzadanie S22IT1S0P2-83: szczegółowe testy wersji roboczej wniosku.

Uzupełnia `test_wersja_robocza.py` o: etapy przygotowania, aktualizację jednej wersji zamiast tworzenia
kolejnych, odporność na błędne dane, brak udziału agentów, widoczność wyłącznie dla wnioskodawcy
i to, że rezygnacja nie dotyka wniosków już złożonych.
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from kwatermistrz.api.main import app, get_broker
from kwatermistrz.api.wnioski import etap_przygotowania
from kwatermistrz.db import sesja
from kwatermistrz.models import Delegacja, Status
from kwatermistrz.web.strony import CIASTECZKO

AUTOZAPIS = "/app/wnioski/nowy/roboczy"
CEL_I_TERMIN = {
    "cel_wyjazdu": "szkolenie",
    "miejsce_docelowe": "Toruń",
    "data_wyjazdu": "2026-12-07",
    "data_powrotu": "2026-12-11",
}


@pytest.fixture
def klient(broker):
    app.dependency_overrides[get_broker] = lambda: broker
    with TestClient(app, follow_redirects=False) as c:
        c.cookies.set(CIASTECZKO, "ZOL-003")
        yield c
    app.dependency_overrides.clear()


def wersje_robocze(id_zolnierza: str = "ZOL-003") -> list[Delegacja]:
    with sesja() as s:
        return list(
            s.scalars(
                select(Delegacja).where(Delegacja.id_zolnierza == id_zolnierza, Delegacja.status == Status.ROBOCZY)
            )
        )


# ---------------------------------------------------------------- etap przygotowania


@pytest.mark.parametrize(
    ("dane", "etap"),
    [
        ({}, "dane wnioskodawcy"),
        ({"srodek_transportu": "kolej"}, "dane wnioskodawcy"),
        ({"cel_wyjazdu": "szkolenie"}, "cel i termin"),
        ({"id_przedsiewziecia": "PRZ-001"}, "cel i termin"),
        ({**CEL_I_TERMIN, "data_powrotu": ""}, "cel i termin"),
        (CEL_I_TERMIN, "preferencje"),
    ],
)
def test_etap_przygotowania(dane, etap):
    assert etap_przygotowania(dane) == etap


# ---------------------------------------------------------------- autozapis


def test_autozapis_aktualizuje_jedna_wersje_i_jej_etap(klient):
    klient.post(AUTOZAPIS, data={"srodek_transportu": "kolej"})
    [pierwsza] = wersje_robocze()
    assert pierwsza.etap_przygotowania == "dane wnioskodawcy"

    klient.post(AUTOZAPIS, data={"cel_wyjazdu": "szkolenie"})
    klient.post(AUTOZAPIS, data=CEL_I_TERMIN)
    [wersja] = wersje_robocze()
    assert wersja.id_delegacji == pierwsza.id_delegacji
    assert wersja.etap_przygotowania == "preferencje"
    assert wersja.dane_wniosku["miejsce_docelowe"] == "Toruń"
    assert wersja.data_ostatniej_zmiany >= pierwsza.data_ostatniej_zmiany


def test_wersja_robocza_nie_jest_zlozonym_wnioskiem(klient, broker):
    klient.post(AUTOZAPIS, data=CEL_I_TERMIN)
    [wersja] = wersje_robocze()
    assert wersja.id_delegacji.startswith("ROB-") and wersja.data_zlozenia is None
    # wersja robocza nie trafia do agentów ani do przełożonego
    assert broker.wyslane == []
    assert wersja.id_zatwierdzajacego is None


def test_autozapis_dane_pojazdu_prywatnego(klient):
    dane = {"srodek_transportu": "samochód prywatny", "nr_rejestracyjny": " WE 12345 ", "pojemnosc_silnika": ""}
    klient.post(AUTOZAPIS, data=dane)
    [wersja] = wersje_robocze()
    assert wersja.dane_wniosku["dane_transportu"] == {"nr_rejestracyjny": "WE 12345"}
    assert wersja.dane_wniosku["potrzeba_zakwaterowania"] is False


def test_nieistniejace_przedsiewziecie_nie_psuje_zapisu(klient):
    r = klient.post(AUTOZAPIS, data={"id_przedsiewziecia": "PRZ-NIE-MA", "cel_wyjazdu": "kurs"})
    assert r.status_code == 200 and "Zapisano wersję roboczą" in r.text
    [wersja] = wersje_robocze()
    assert wersja.id_przedsiewziecia is None
    assert wersja.dane_wniosku["id_przedsiewziecia"] == "PRZ-NIE-MA"


def test_autozapis_wymaga_zalogowania(klient):
    klient.cookies.clear()
    r = klient.post(AUTOZAPIS, data=CEL_I_TERMIN)
    assert r.status_code == 303 and r.headers["location"] == "/app/logowanie"
    with sesja() as s:
        assert not s.scalars(select(Delegacja).where(Delegacja.status == Status.ROBOCZY)).all()


# ---------------------------------------------------------------- wznowienie i widoczność


def test_wersje_robocze_sa_osobne_dla_kazdego_wnioskodawcy(klient):
    klient.post(AUTOZAPIS, data={"cel_wyjazdu": "kurs A"})
    klient.cookies.set(CIASTECZKO, "ZOL-004")
    klient.post(AUTOZAPIS, data={"cel_wyjazdu": "kurs B"})

    assert [w.dane_wniosku["cel_wyjazdu"] for w in wersje_robocze("ZOL-003")] == ["kurs A"]
    assert [w.dane_wniosku["cel_wyjazdu"] for w in wersje_robocze("ZOL-004")] == ["kurs B"]
    strona = klient.get("/app/wnioski/nowy").text
    assert 'value="kurs B"' in strona and "kurs A" not in strona


def test_wznowiony_formularz_ma_wszystkie_zapisane_pola(klient):
    dane = {**CEL_I_TERMIN, "podstawa_numer_rozkazu": "Rozkaz 215/2026", "srodek_transportu": "kolej"}
    klient.post(AUTOZAPIS, data=dane)
    strona = klient.get("/app/wnioski/nowy").text
    assert "Masz niezłożony wniosek" in strona and "etap: preferencje" in strona
    for wartosc in ("szkolenie", "Toruń", "2026-12-07", "2026-12-11", "Rozkaz 215/2026"):
        assert f'value="{wartosc}"' in strona


def test_pulpit_bez_wersji_roboczej_nie_pokazuje_banera(klient):
    strona = klient.get("/app/pulpit").text
    assert "Dokończ wniosek" not in strona and "Masz niezłożony wniosek" not in strona


# ---------------------------------------------------------------- rezygnacja


def test_rezygnacja_nie_usuwa_zlozonych_wnioskow(klient):
    with sesja() as s:
        s.add(Delegacja(id_delegacji="DEL-ZL-1", id_zolnierza="ZOL-003", dane_wniosku={}, status=Status.ZLOZONY))
    klient.post(AUTOZAPIS, data={"cel_wyjazdu": "kurs"})

    r = klient.post("/app/wnioski/nowy/roboczy/rezygnacja")
    assert r.status_code == 200 and "Usunięto wersję roboczą" in r.text
    assert wersje_robocze() == []
    with sesja() as s:
        assert s.get(Delegacja, "DEL-ZL-1").status == Status.ZLOZONY


def test_rezygnacja_bez_wersji_roboczej(klient):
    r = klient.post("/app/wnioski/nowy/roboczy/rezygnacja")
    assert r.status_code == 200 and "Masz niezłożony wniosek" not in r.text


# ---------------------------------------------------------------- API


def test_api_wersji_roboczej_bledy(klient):
    assert klient.put("/wnioskodawcy/ZOL-BRAK/wersja-robocza", json={"dane": {}}).status_code == 404
    assert klient.delete("/wnioskodawcy/ZOL-003/wersja-robocza").status_code == 404
    r = klient.put("/wnioskodawcy/ZOL-003/wersja-robocza", json={"dane": {}, "etap_przygotowania": "gotowe"})
    assert r.status_code == 422
    assert wersje_robocze() == []


def test_api_jawny_etap_i_widocznosc_tylko_wlasnej_wersji(klient):
    r = klient.put(
        "/wnioskodawcy/ZOL-003/wersja-robocza",
        json={"dane": {"cel_wyjazdu": "kurs"}, "etap_przygotowania": "podsumowanie"},
    )
    assert r.status_code == 200 and r.json()["etap_przygotowania"] == "podsumowanie"
    assert klient.get("/wnioskodawcy/ZOL-003/wersja-robocza").json()["dane"] == {"cel_wyjazdu": "kurs"}
    assert klient.get("/wnioskodawcy/ZOL-004/wersja-robocza").status_code == 404
