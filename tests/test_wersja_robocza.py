"""S22IT1S0P2-37: wersja robocza wniosku – autozapis (-79), wznowienie (-80), usunięcie po złożeniu i rezygnacji (-81).

Szczegółowe testy wersji roboczej – podzadanie S22IT1S0P2-83.
"""

import pytest
from fastapi.testclient import TestClient
from pomoc_sesji import zaloguj_z_csrf
from sqlalchemy import select

from kwatermistrz.api.main import app, get_broker
from kwatermistrz.db import sesja
from kwatermistrz.models import Delegacja, Status

CZESC = {"cel_wyjazdu": "szkolenie", "miejsce_docelowe": "Toruń", "srodek_transportu": "kolej"}
CALOSC = {
    **CZESC,
    "data_wyjazdu": "2026-12-07",
    "data_powrotu": "2026-12-11",
    "podstawa_numer_rozkazu": "Rozkaz dzienny nr 215/2026",
    "podstawa_data_rozkazu": "2026-11-10",
    "potrzeba_zakwaterowania": "true",
}


@pytest.fixture
def klient(broker):
    app.dependency_overrides[get_broker] = lambda: broker
    with TestClient(app, follow_redirects=False) as c:
        zaloguj_z_csrf(c, "ZOL-003")
        yield c
    app.dependency_overrides.clear()


def wersje_robocze() -> list[Delegacja]:
    with sesja() as s:
        return list(s.scalars(select(Delegacja).where(Delegacja.status == Status.ROBOCZY)))


def test_autozapis_zachowuje_dane_jako_niezlozony_wniosek(klient):
    odp = klient.post("/app/wnioski/nowy/roboczy", data=CZESC)
    assert odp.status_code == 200 and "Zapisano wersję roboczą" in odp.text
    klient.post("/app/wnioski/nowy/roboczy", data={**CZESC, "data_wyjazdu": "2026-12-07"})

    (d,) = wersje_robocze()  # ciągle jedna wersja robocza – aktualizowana, nie dublowana
    assert d.id_zolnierza == "ZOL-003" and d.data_zlozenia is None and d.data_ostatniej_zmiany is not None
    assert d.etap_przygotowania == "cel i termin" and d.dane_wniosku["data_wyjazdu"] == "2026-12-07"

    r = klient.post("/app/wnioski/nowy/roboczy?wyjdz=1", data=CZESC)
    assert r.status_code == 303 and "wersja-robocza" in r.headers["location"]


def test_wznowienie_tylko_dla_wnioskodawcy(klient):
    klient.post("/app/wnioski/nowy/roboczy", data=CZESC)

    formularz = klient.get("/app/wnioski/nowy").text
    assert "Masz niezłożony wniosek" in formularz and 'value="Toruń"' in formularz
    pulpit = klient.get("/app/pulpit").text
    assert "Dokończ wniosek" in pulpit and "/app/wnioski/ROB-" not in pulpit

    zaloguj_z_csrf(klient, "ZOL-004")
    assert "Masz niezłożony wniosek" not in klient.get("/app/wnioski/nowy").text


def test_podsumowanie_zapisuje_etap_a_zlozenie_usuwa_wersje_robocza(klient):
    klient.post("/app/wnioski/nowy/podsumowanie", data=CALOSC)
    (d,) = wersje_robocze()
    assert d.etap_przygotowania == "podsumowanie"

    assert klient.post("/app/wnioski/nowy", data=CALOSC).status_code == 303
    assert wersje_robocze() == []
    with sesja() as s:
        assert s.scalar(select(Delegacja)).status == Status.ZLOZONY


def test_rezygnacja_usuwa_dane_i_pokazuje_nowy_wniosek(klient):
    klient.post("/app/wnioski/nowy/roboczy", data=CZESC)
    strona = klient.post("/app/wnioski/nowy/roboczy/rezygnacja").text
    assert "Usunięto wersję roboczą" in strona and 'value="Toruń"' not in strona
    assert wersje_robocze() == []


def test_api_wersji_roboczej(klient):
    url = "/wnioskodawcy/ZOL-003/wersja-robocza"
    assert klient.get(url).status_code == 404
    odp = klient.put(url, json={"dane": {"cel_wyjazdu": "kurs"}})
    assert odp.status_code == 200 and odp.json()["etap_przygotowania"] == "cel i termin"
    assert klient.get(url).json()["dane"]["cel_wyjazdu"] == "kurs"
    assert klient.delete(url).status_code == 200
    assert klient.get(url).status_code == 404
