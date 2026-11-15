"""Ekrany organizacji wyjazdu: wybór przejazdu (S22IT1S0P2-27, podzadanie -45) i zakwaterowania
(S22IT1S0P2-39, podzadanie -99) z zapisem rezerwacji.

Propozycje, które w docelowym przebiegu przygotowują agenci (-27, -97, -98), test zapisuje bezpośrednio.
"""

import asyncio
from datetime import date, time
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from kwatermistrz.agents import AGENCI
from kwatermistrz.agents.rezerwacja import TYP_DECYZJI
from kwatermistrz.api.main import app, get_broker
from kwatermistrz.config import settings
from kwatermistrz.db import sesja
from kwatermistrz.messaging import Typ
from kwatermistrz.models import (
    Delegacja,
    LogDecyzji,
    LogOperacji,
    ObiektZakwaterowania,
    Polaczenie,
    Przejazd,
    Status,
    StatusRezerwacji,
    Zakwaterowanie,
)
from kwatermistrz.sesje import CIASTECZKO, token_csrf

DEL = "DEL-ORG-1"


@pytest.fixture
def klient(broker):
    app.dependency_overrides[get_broker] = lambda: broker
    with TestClient(app, follow_redirects=False) as c:
        c.post("/app/logowanie", data={"id_zolnierza": "ZOL-003"})
        c.headers["X-CSRF-Token"] = token_csrf(c.cookies[CIASTECZKO], settings.session_secret.get_secret_value())
        yield c
    app.dependency_overrides.clear()


def dostarcz(broker) -> None:
    asyncio.run(broker.dostarczaj([klasa(broker) for klasa in AGENCI.values()]))


def przygotuj(status: str = Status.ZATWIERDZONY) -> dict[str, int]:
    """Zatwierdzona delegacja z propozycjami przejazdu i zakwaterowania."""
    with sesja() as s:
        s.add(
            Delegacja(
                id_delegacji=DEL,
                id_zolnierza="ZOL-003",
                id_zatwierdzajacego="ZOL-001",
                miejsce_docelowe="Toruń",
                dane_wniosku={},
                status=status,
                potrzeba_zakwaterowania=True,
                data_wyjazdu=date(2026, 12, 7),
                data_powrotu=date(2026, 12, 11),
            )
        )
        s.add_all(
            [
                ObiektZakwaterowania(
                    id_obiektu="OB-TEST-1",
                    nazwa="Hotel garnizonowy Testowo",
                    rodzaj="Wojskowy",
                    miejscowosc="Toruń",
                    cena_za_dobe=Decimal("120.00"),
                    liczba_wolnych_miejsc=1,
                    czas_dojazdu_min=15,
                ),
                ObiektZakwaterowania(
                    id_obiektu="OB-TEST-2",
                    nazwa="Hotel Centrum Testowo",
                    rodzaj="Cywilny",
                    miejscowosc="Toruń",
                    cena_za_dobe=Decimal("310.00"),
                    liczba_wolnych_miejsc=5,
                    czas_dojazdu_min=10,
                ),
            ]
        )
        s.flush()
        for ident, transport, odjazd, cena in [
            ("ORG-IC-T", "kolej", time(6), "89"),
            ("ORG-PKS-T", "autobus", time(5), "100"),
            ("ORG-IC-P", "kolej", time(17), "89"),
        ]:
            s.add(
                Polaczenie(
                    id_polaczenia=ident,
                    skad="Toruń" if ident.endswith("P") else "Warszawa",
                    dokad="Warszawa" if ident.endswith("P") else "Toruń",
                    srodek_transportu=transport,
                    przewoznik=ident,
                    godzina_odjazdu=odjazd,
                    godzina_przyjazdu=time(8),
                    czas_min=120,
                    koszt=Decimal(cena),
                    liczba_przesiadek=0,
                    punktualnosc=0.9,
                    ocena_bezpieczenstwa=5,
                )
            )
        s.flush()
        rekomendowany = Przejazd(
            id_delegacji=DEL,
            kierunek="Tam",
            srodek_transportu="kolej",
            numer_kursu="IC 5312",
            id_polaczenia="ORG-IC-T",
            data_przejazdu=date(2026, 12, 7),
            koszt=Decimal("89.00"),
            status=StatusRezerwacji.PROPOZYCJA,
            rekomendowany=True,
            uzasadnienie="Najkrótszy czas i przyjazd przed rozpoczęciem zajęć",
        )
        inny = Przejazd(
            id_delegacji=DEL,
            kierunek="Tam",
            srodek_transportu="autobus",
            numer_kursu="PKS 101",
            id_polaczenia="ORG-PKS-T",
            data_przejazdu=date(2026, 12, 7),
            koszt=Decimal("100.00"),
            status=StatusRezerwacji.PROPOZYCJA,
        )
        powrot = Przejazd(
            id_delegacji=DEL,
            kierunek="Powrót",
            srodek_transportu="kolej",
            numer_kursu="IC 5313",
            id_polaczenia="ORG-IC-P",
            data_przejazdu=date(2026, 12, 11),
            koszt=Decimal("89.00"),
            status=StatusRezerwacji.PROPOZYCJA,
            rekomendowany=True,
        )
        w_limicie = Zakwaterowanie(
            id_delegacji=DEL,
            id_obiektu="OB-TEST-1",
            data_od=date(2026, 12, 7),
            data_do=date(2026, 12, 11),
            cena_za_dobe=Decimal("120.00"),
            koszt_calkowity=Decimal("480.00"),
            status=StatusRezerwacji.PROPOZYCJA,
            czas_dojazdu_min=15,
        )
        z_odstepstwem = Zakwaterowanie(
            id_delegacji=DEL,
            id_obiektu="OB-TEST-2",
            data_od=date(2026, 12, 7),
            data_do=date(2026, 12, 11),
            cena_za_dobe=Decimal("310.00"),
            koszt_calkowity=Decimal("1240.00"),
            status=StatusRezerwacji.PROPOZYCJA,
            czas_dojazdu_min=10,
            wymaga_odstepstwa=True,
        )
        s.add_all([rekomendowany, inny, powrot, w_limicie, z_odstepstwem])
        s.flush()
        return {
            "rekomendowany": rekomendowany.id_przejazdu,
            "inny": inny.id_przejazdu,
            "powrot": powrot.id_przejazdu,
            "w_limicie": w_limicie.id_zakwaterowania,
            "z_odstepstwem": z_odstepstwem.id_zakwaterowania,
        }


def decyzje_rezerwacji() -> list[LogDecyzji]:
    """Decyzje zapisu rezerwacji w logu (komunikaty wysłane przez agentów broker zużywa przy dostarczaniu)."""
    with sesja() as s:
        return list(
            s.scalars(
                select(LogDecyzji)
                .join(LogOperacji, LogOperacji.id_operacji == LogDecyzji.id_operacji)
                .where(LogOperacji.id_delegacji == DEL, LogDecyzji.typ_decyzji == TYP_DECYZJI)
            )
        )


def wybory(broker, typ: str) -> list[dict]:
    return [k.dane for k in broker.wyslane if k.typ == typ]


def test_ekran_przejazdu_porownuje_warianty_z_rekomendacja(klient):
    ids = przygotuj()
    strona = klient.get(f"/app/wnioski/{DEL}/przejazd").text
    assert "Wybór przejazdu" in strona and "Tam" in strona and "Powrót" in strona
    assert "IC 5312" in strona and "PKS 101" in strona and "rekomendowany" in strona
    assert f'name="wybor_1" value="{ids["rekomendowany"]}" required checked' in strona
    assert "Żaden wariant mi nie odpowiada" in strona


def test_wybor_innego_niz_rekomendowany_wymaga_przyczyny(klient, broker):
    ids = przygotuj()
    dane = {"wybor_1": str(ids["inny"]), "wybor_2": str(ids["powrot"])}
    r = klient.post(f"/app/wnioski/{DEL}/przejazd", data=dane)
    assert r.status_code == 200 and "podaj przyczynę wyboru" in r.text
    assert not wybory(broker, Typ.WYBOR_TRANSPORTU)

    r = klient.post(f"/app/wnioski/{DEL}/przejazd", data={**dane, "uzasadnienie": "tańszy wariant"})
    assert r.status_code == 303
    assert "Wybór przekazany" in klient.get(r.headers["location"]).text
    assert wybory(broker, Typ.WYBOR_TRANSPORTU) == [
        {
            "id_tam": ids["inny"],
            "id_powrot": ids["powrot"],
            "uzasadnienie": "tańszy wariant",
            "odrzuc_wszystkie": False,
            "kryterium": "standardowe",
            "zgoda_na_zmiane_transportu": False,
            "id_osoby": "ZOL-003",
        }
    ]


def test_brak_wyboru_dla_kierunku(klient, broker):
    ids = przygotuj()
    r = klient.post(f"/app/wnioski/{DEL}/przejazd", data={"wybor_1": str(ids["rekomendowany"])})
    assert r.status_code == 200 and "Wybierz wariant przejazdu: powrót" in r.text
    assert not wybory(broker, Typ.WYBOR_TRANSPORTU)


def test_odrzucenie_wszystkich_wariantow(klient, broker):
    przygotuj()
    r = klient.post(f"/app/wnioski/{DEL}/przejazd/odrzucenie", data={"przyczyna": " "})
    assert r.status_code == 200 and "Podaj przyczynę odrzucenia" in r.text
    r = klient.post(f"/app/wnioski/{DEL}/przejazd/odrzucenie", data={"przyczyna": "dojazd własny"})
    assert r.status_code == 303
    assert wybory(broker, Typ.WYBOR_TRANSPORTU) == [
        {
            "id_tam": None,
            "id_powrot": None,
            "odrzuc_wszystkie": True,
            "uzasadnienie": "dojazd własny",
            "kryterium": "standardowe",
            "zgoda_na_zmiane_transportu": False,
            "id_osoby": "ZOL-003",
        }
    ]


def test_rezerwacja_obiektu_w_limicie(klient, broker):
    ids = przygotuj()
    strona = klient.get(f"/app/wnioski/{DEL}/zakwaterowanie").text
    assert "Obiekty w limicie noclegowym" in strona and "Hotel garnizonowy Testowo" in strona
    assert "Warianty wymagające odstępstwa" in strona and "15 min" in strona

    r = klient.post(f"/app/wnioski/{DEL}/zakwaterowanie", data={"id_zakwaterowania": str(ids["w_limicie"])})
    assert r.status_code == 303
    dostarcz(broker)

    with sesja() as s:
        z = s.get(Zakwaterowanie, ids["w_limicie"])
        assert z.status == StatusRezerwacji.WYBRANA and "w limicie" in z.uzasadnienie
        assert s.get(ObiektZakwaterowania, "OB-TEST-1").liczba_wolnych_miejsc == 0
    [decyzja] = decyzje_rezerwacji()
    assert decyzja.wybrany_wariant == "Hotel garnizonowy Testowo" and not decyzja.wymaga_zatwierdzenia
    strona = klient.get(f"/app/wnioski/{DEL}/zakwaterowanie").text
    assert "Zarezerwowano zakwaterowanie" in strona and 'name="id_zakwaterowania"' not in strona


def test_odstepstwo_wymaga_uzasadnienia_i_czeka_na_przelozonego(klient, broker):
    ids = przygotuj()
    dane = {"id_zakwaterowania": str(ids["z_odstepstwem"])}
    r = klient.post(f"/app/wnioski/{DEL}/zakwaterowanie", data=dane)
    assert r.status_code == 200 and "podaj uzasadnienie" in r.text
    assert not wybory(broker, Typ.WYBOR_ZAKWATEROWANIA)

    r = klient.post(f"/app/wnioski/{DEL}/zakwaterowanie", data={**dane, "uzasadnienie": "brak miejsc w limicie"})
    assert r.status_code == 303
    dostarcz(broker)

    with sesja() as s:
        z = s.get(Zakwaterowanie, ids["z_odstepstwem"])
        assert z.status == StatusRezerwacji.DO_DECYZJI_PRZELOZONEGO and z.uzasadnienie == "brak miejsc w limicie"
    [decyzja] = decyzje_rezerwacji()
    assert decyzja.wybrany_wariant == "Hotel Centrum Testowo" and decyzja.wymaga_zatwierdzenia
    assert "czeka na decyzję przełożonego" in klient.get(f"/app/wnioski/{DEL}/zakwaterowanie").text


def test_dostep_tylko_dla_delegowanego_na_etapie_organizacji(klient):
    przygotuj(status=Status.ZLOZONY)
    assert klient.get(f"/app/wnioski/{DEL}/przejazd").status_code == 409
    assert "Organizacja wyjazdu" not in klient.get(f"/app/wnioski/{DEL}").text
    with sesja() as s:
        s.get(Delegacja, DEL).status = Status.ZATWIERDZONY
    strona = klient.get(f"/app/wnioski/{DEL}").text
    assert "Organizacja wyjazdu" in strona and f"/app/wnioski/{DEL}/zakwaterowanie" in strona

    klient.post("/app/logowanie", data={"id_zolnierza": "ZOL-004"})
    assert klient.get(f"/app/wnioski/{DEL}/przejazd").status_code == 404
    assert klient.get(f"/app/wnioski/{DEL}/zakwaterowanie").status_code == 404


def test_bez_propozycji_ekran_informuje_o_przygotowaniu(klient):
    with sesja() as s:
        s.add(Delegacja(id_delegacji=DEL, id_zolnierza="ZOL-003", dane_wniosku={}, status=Status.ZATWIERDZONY))
    assert "System przygotowuje warianty przejazdu" in klient.get(f"/app/wnioski/{DEL}/przejazd").text
    assert "System dobiera obiekty" in klient.get(f"/app/wnioski/{DEL}/zakwaterowanie").text
    with sesja() as s:
        assert not s.scalars(select(Zakwaterowanie).where(Zakwaterowanie.id_delegacji == DEL)).all()


def test_wybor_delegowanego_z_nowego_ekranu_zapisuje_obydwa_przejazdy(klient, broker):
    ids = przygotuj()
    r = klient.post(
        f"/app/wnioski/{DEL}/przejazd",
        data={
            "wybor_1": ids["rekomendowany"],
            "wybor_2": ids["powrot"],
            "id_osoby": "ZOL-001",
        },
    )
    assert r.status_code == 303
    assert wybory(broker, Typ.WYBOR_TRANSPORTU)[-1]["id_osoby"] == "ZOL-003"  # tożsamość z sesji
    dostarcz(broker)
    with sesja() as s:
        wybrane = list(
            s.scalars(select(Przejazd).where(Przejazd.id_delegacji == DEL, Przejazd.status == StatusRezerwacji.WYBRANA))
        )
        assert {p.kierunek for p in wybrane} == {"Tam", "Powrót"}
        wpis = s.scalar(
            select(LogDecyzji)
            .join(LogOperacji)
            .where(LogOperacji.id_delegacji == DEL, LogDecyzji.typ_decyzji == "Zapis wyboru podróży")
        )
        assert wpis is not None and "ZOL-003" in wpis.uzasadnienie


def test_nowy_ekran_transportu_odrzuca_brak_csrf(klient, broker):
    ids = przygotuj()
    del klient.headers["X-CSRF-Token"]
    r = klient.post(
        f"/app/wnioski/{DEL}/przejazd",
        data={
            "wybor_1": ids["rekomendowany"],
            "wybor_2": ids["powrot"],
        },
    )
    assert r.status_code == 403
    assert not wybory(broker, Typ.WYBOR_TRANSPORTU)


def test_nowy_ekran_wymaga_zgody_na_zmiane_transportu(klient, broker):
    ids = przygotuj()
    with sesja() as s:
        s.get(Delegacja, DEL).srodek_transportu = "pojazd służbowy"
    dane = {"wybor_1": ids["rekomendowany"], "wybor_2": ids["powrot"], "uzasadnienie": "Uzgodniono kolej"}
    r = klient.post(f"/app/wnioski/{DEL}/przejazd", data=dane)
    assert r.status_code == 200 and "zgodę na zmianę" in r.text
    assert not wybory(broker, Typ.WYBOR_TRANSPORTU)
    r = klient.post(f"/app/wnioski/{DEL}/przejazd", data=dane | {"zgoda_na_zmiane_transportu": "true"})
    assert r.status_code == 303
    dostarcz(broker)
    with sesja() as s:
        assert sum(p.status == StatusRezerwacji.WYBRANA for p in s.scalars(select(Przejazd))) == 2


def test_ekran_przelicza_rekomendacje_gdy_zapisana_para_ma_bledne_godziny(klient, broker):
    ids = przygotuj()
    with sesja() as s:
        s.get(Delegacja, DEL).data_powrotu = date(2026, 12, 7)
        s.get(Przejazd, ids["powrot"]).data_przejazdu = date(2026, 12, 7)
        pol = s.get(Polaczenie, "ORG-IC-T")
        pol.godzina_odjazdu = time(16)
        pol.czas_min = 150
    html = klient.get(f"/app/wnioski/{DEL}/przejazd").text
    assert f'name="wybor_1" value="{ids["rekomendowany"]}" required checked' not in html
    assert f'name="wybor_1" value="{ids["inny"]}" required checked' in html
    r = klient.post(
        f"/app/wnioski/{DEL}/przejazd",
        data={
            "wybor_1": ids["rekomendowany"],
            "wybor_2": ids["powrot"],
            "uzasadnienie": "Test złej pary",
        },
    )
    assert r.status_code == 200 and "17:00" in r.text and "18:30" in r.text
    assert not wybory(broker, Typ.WYBOR_TRANSPORTU)
