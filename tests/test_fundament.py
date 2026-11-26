"""Fundament sprintu 1: migracje bazy, dane słownikowe, przebieg delegacji w Koordynatorze."""

import asyncio
from datetime import timedelta
from decimal import Decimal

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, func, inspect, select, text

from kwatermistrz.agents import AGENCI
from kwatermistrz.db import Base, migruj, sesja, teraz
from kwatermistrz.messaging import Komunikat, Typ
from kwatermistrz.models import (
    Delegacja,
    LogOperacji,
    ObiektZakwaterowania,
    Polaczenie,
    PolecenieWyjazdu,
    Przejazd,
    Status,
    StatusRezerwacji,
    Zakwaterowanie,
    Zolnierz,
)
from kwatermistrz.seed import zaladuj_dane_poczatkowe

# ---------------------------------------------------------------- migracje


def test_migracje_odpowiadaja_modelom(tmp_path):
    silnik = create_engine(f"sqlite:///{(tmp_path / 'm.db').as_posix()}")
    migruj(silnik)
    with silnik.connect() as polaczenie:
        roznice = compare_metadata(MigrationContext.configure(polaczenie), Base.metadata)
    assert roznice == [], "models.py różni się od migracji – wygeneruj nową: alembic revision --autogenerate"


def test_migracja_bazy_sprzed_sprintu_1_zachowuje_dane(tmp_path):
    """Baza w Azure powstała przed migracjami – musi przejść na nowy schemat bez utraty delegacji."""
    silnik = create_engine(f"sqlite:///{(tmp_path / 'stara.db').as_posix()}")
    cfg = Config()
    cfg.set_main_option("script_location", "src/kwatermistrz/migracje")
    with silnik.begin() as polaczenie:
        cfg.attributes["polaczenie"] = polaczenie
        command.upgrade(cfg, "0001")
        polaczenie.exec_driver_sql("DROP TABLE alembic_version")  # tak wygląda baza utworzona przez create_all
        polaczenie.execute(text("INSERT INTO jednostka VALUES ('JW-1', 'Jednostka', 'Warszawa', 1000)"))
        polaczenie.execute(
            text(
                "INSERT INTO zolnierz (id_zolnierza, stopien, imie_nazwisko, id_jednostki)"
                " VALUES ('Z1', 'kpr.', 'X Y', 'JW-1')"
            )
        )
        polaczenie.execute(
            text(
                "INSERT INTO delegacja (id_delegacji, id_zolnierza, dane_wniosku, data_zlozenia, status,"
                " liczba_przypomnien, przekazano_zastepcy)"
                " VALUES ('D1', 'Z1', '{}', '2026-11-12 10:00:00', 'Oczekuje na akceptację', 0, 0)"
            )
        )

    migruj(silnik)

    with silnik.connect() as polaczenie:
        status, od_kiedy, id_jednostki = polaczenie.execute(
            text("SELECT status, data_zmiany_statusu, id_jednostki FROM delegacja WHERE id_delegacji = 'D1'")
        ).one()
        historia = polaczenie.execute(
            text("SELECT id_jednostki, data_od, data_do, zrodlo FROM przynaleznosc_jednostki WHERE id_zolnierza = 'Z1'")
        ).one()
        wersja = polaczenie.execute(text("SELECT version_num FROM alembic_version")).scalar()
    assert status == Status.W_AKCEPTACJI
    assert od_kiedy is not None
    assert id_jednostki == "JW-1"
    assert historia[0] == "JW-1" and historia[1] is not None and historia[2] is None and historia[3] == "migracja"
    assert wersja == "0005"


def test_downgrade_historii_nie_uszkadza_innych_tabel(tmp_path):
    silnik = create_engine(f"sqlite:///{(tmp_path / 'downgrade.db').as_posix()}")
    with silnik.begin() as polaczenie:
        cfg = konfiguracja_migracji(polaczenie)
        command.upgrade(cfg, "head")
        command.downgrade(cfg, "0004")
        tabele = inspect(polaczenie).get_table_names()
        kolumny_delegacji = {kolumna["name"] for kolumna in inspect(polaczenie).get_columns("delegacja")}
        wersja = polaczenie.execute(text("SELECT version_num FROM alembic_version")).scalar()
    assert "przynaleznosc_jednostki" not in tabele
    assert {"jednostka", "zolnierz", "delegacja"} <= set(tabele)
    assert "id_jednostki" not in kolumny_delegacji
    assert wersja == "0004"


def konfiguracja_migracji(polaczenie):
    cfg = Config()
    cfg.set_main_option("script_location", "src/kwatermistrz/migracje")
    cfg.attributes["polaczenie"] = polaczenie
    return cfg


def dodaj_stary_dokument(polaczenie, id_dokumentu: int, rodzaj: str, numer: str, klucz: str) -> None:
    polaczenie.execute(
        text(
            "INSERT INTO dokument_kosztowy "
            "(id_dokumentu, id_rozliczenia, rodzaj, numer, data, kwota, przekracza_limit, "
            "klucz_unikalnosci, data_przekazania) "
            "VALUES (:id, 999, :rodzaj, :numer, '2026-11-12', 123.40, 0, :klucz, '2026-11-15 12:00:00')"
        ),
        {"id": id_dokumentu, "rodzaj": rodzaj, "numer": numer, "klucz": klucz},
    )


def test_migracja_kluczy_dokumentow_zachowuje_istniejace_rekordy(tmp_path):
    silnik = create_engine(f"sqlite:///{(tmp_path / 'dokumenty.db').as_posix()}")
    with silnik.begin() as polaczenie:
        cfg = konfiguracja_migracji(polaczenie)
        command.upgrade(cfg, "0003")
        dodaj_stary_dokument(polaczenie, 1, "Bilet", " IC   12345 ", "bilet|ic 12345|2026-11-12|123.40")
        dodaj_stary_dokument(polaczenie, 2, "Faktura", "FV/77", "faktura|fv/77|2026-11-12|123.40")
        command.upgrade(cfg, "head")

    with silnik.connect() as polaczenie:
        dokumenty = polaczenie.execute(
            text("SELECT id_dokumentu, klucz_unikalnosci FROM dokument_kosztowy ORDER BY id_dokumentu")
        ).all()
    assert dokumenty == [(1, "ic 12345|2026-11-12|123.40"), (2, "fv/77|2026-11-12|123.40")]


def test_migracja_kluczy_przerywa_przy_kolizji_i_nie_zmienia_rekordow(tmp_path):
    silnik = create_engine(f"sqlite:///{(tmp_path / 'kolizja.db').as_posix()}")
    with silnik.begin() as polaczenie:
        cfg = konfiguracja_migracji(polaczenie)
        command.upgrade(cfg, "0003")
        dodaj_stary_dokument(polaczenie, 1, "Bilet", "IC 12345", "bilet|ic 12345|2026-11-12|123.40")
        dodaj_stary_dokument(polaczenie, 2, "Faktura", "ic 12345", "faktura|ic 12345|2026-11-12|123.40")

    with pytest.raises(RuntimeError, match="kolizj"):
        with silnik.begin() as polaczenie:
            command.upgrade(konfiguracja_migracji(polaczenie), "head")

    with silnik.connect() as polaczenie:
        dokumenty = (
            polaczenie.execute(text("SELECT klucz_unikalnosci FROM dokument_kosztowy ORDER BY id_dokumentu"))
            .scalars()
            .all()
        )
        wersja = polaczenie.execute(text("SELECT version_num FROM alembic_version")).scalar()
    assert dokumenty == ["bilet|ic 12345|2026-11-12|123.40", "faktura|ic 12345|2026-11-12|123.40"]
    assert wersja == "0003"


# ---------------------------------------------------------------- dane słownikowe


def test_dane_poczatkowe_mozna_ladowac_wielokrotnie():
    with sesja() as s:
        zaladuj_dane_poczatkowe(s)
        zaladuj_dane_poczatkowe(s)
    with sesja() as s:
        assert s.get(Zolnierz, "ZOL-001").id_zastepcy == "ZOL-002"
        assert s.scalar(select(func.count()).select_from(Polaczenie)) >= 5
        obiekt = s.get(ObiektZakwaterowania, "OBJ-03")
        assert obiekt.czas_dojazdu_min is not None and obiekt.liczba_wolnych_miejsc > 0


# ---------------------------------------------------------------- Koordynator


def utworz(id_d="DEL-F-1", **pola) -> str:
    with sesja() as s:
        s.add(Delegacja(id_delegacji=id_d, id_zolnierza="ZOL-003", dane_wniosku={}, **pola))
    return id_d


def status(id_d: str) -> str:
    with sesja() as s:
        return s.get(Delegacja, id_d).status


def wynik_zatwierdzenia(broker, id_d, decyzja):
    broker.wyslane.append(
        Komunikat(typ=Typ.WYNIK_ZATWIERDZENIA, id_delegacji=id_d, dane={"decyzja": decyzja, "id_osoby": "ZOL-001"})
    )
    koordynator = AGENCI["koordynator"](broker)
    k = broker.wyslane.pop()
    asyncio.run(koordynator.wykonaj(k, *koordynator.obslugi()[k.typ]))


def test_po_zatwierdzeniu_zlecane_sa_polecenie_transport_i_zakwaterowanie(broker):
    id_d = utworz(status=Status.W_AKCEPTACJI)
    wynik_zatwierdzenia(broker, id_d, "Zgoda")
    assert status(id_d) == Status.ZATWIERDZONY
    assert {Typ.ZADANIE_POLECENIA, Typ.ZADANIE_TRANSPORTU, Typ.ZADANIE_ZAKWATEROWANIA} <= set(broker.typy())


def test_bez_potrzeby_zakwaterowania_nie_zlecamy_noclegu(broker):
    id_d = utworz(status=Status.W_AKCEPTACJI, potrzeba_zakwaterowania=False)
    wynik_zatwierdzenia(broker, id_d, "Zgoda")
    assert Typ.ZADANIE_ZAKWATEROWANIA not in broker.typy()


def test_decyzja_do_uzupelnienia(broker):
    id_d = utworz(status=Status.W_AKCEPTACJI)
    wynik_zatwierdzenia(broker, id_d, "Do uzupełnienia")
    assert status(id_d) == Status.DO_UZUPELNIENIA


def test_delegacja_zorganizowana_gdy_sa_polecenie_przejazd_i_nocleg(broker):
    id_d = utworz(status=Status.ZATWIERDZONY, data_wyjazdu=teraz().date() + timedelta(days=5))
    koordynator = AGENCI["koordynator"](broker)

    def wynik(typ):
        k = Komunikat(typ=typ, id_delegacji=id_d)
        asyncio.run(koordynator.wykonaj(k, *koordynator.obslugi()[typ]))

    with sesja() as s:
        s.add(PolecenieWyjazdu(numer="PW/2026/0001", id_delegacji=id_d, wersja_wzoru="2026-1", tresc={}))
    wynik(Typ.WYNIK_POLECENIA)
    assert status(id_d) == Status.ZATWIERDZONY

    dzien = teraz().date()
    with sesja() as s:
        s.add(
            Przejazd(
                id_delegacji=id_d,
                kierunek="Tam",
                srodek_transportu="kolej",
                data_przejazdu=dzien,
                status=StatusRezerwacji.WYBRANA,
                id_polaczenia="POL-02",
            )
        )
        s.add(
            Zakwaterowanie(
                id_delegacji=id_d,
                id_obiektu="OBJ-03",
                data_od=dzien,
                data_do=dzien,
                cena_za_dobe=Decimal("260"),
                koszt_calkowity=Decimal("1040"),
                status=StatusRezerwacji.WYBRANA,
            )
        )
    wynik(Typ.WYNIK_ZAKWATEROWANIA)
    assert status(id_d) == Status.ZATWIERDZONY  # brakuje jeszcze powrotu
    with sesja() as s:
        s.add(
            Przejazd(
                id_delegacji=id_d,
                kierunek="Powrót",
                srodek_transportu="kolej",
                data_przejazdu=dzien,
                status=StatusRezerwacji.WYBRANA,
                id_polaczenia="POL-02-P",
            )
        )
    wynik(Typ.WYNIK_TRANSPORTU)
    assert status(id_d) == Status.ZORGANIZOWANY


def test_daty_przesuwaja_delegacje_w_trakcie_i_do_rozliczenia(broker):
    dzis = teraz().date()
    trwa = utworz(
        "DEL-F-T",
        status=Status.ZORGANIZOWANY,
        data_wyjazdu=dzis,
        data_powrotu=dzis + timedelta(days=2),
        potrzeba_zakwaterowania=False,
    )
    zakonczona = utworz(
        "DEL-F-Z",
        status=Status.ZORGANIZOWANY,
        data_wyjazdu=dzis - timedelta(days=5),
        data_powrotu=dzis - timedelta(days=1),
        potrzeba_zakwaterowania=False,
    )
    with sesja() as s:
        for id_d in (trwa, zakonczona):
            s.add(PolecenieWyjazdu(numer=f"PW/{id_d}", id_delegacji=id_d, wersja_wzoru="2026-1", tresc={}))
            for kierunek in ("Tam", "Powrót"):
                s.add(
                    Przejazd(
                        id_delegacji=id_d,
                        kierunek=kierunek,
                        srodek_transportu="kolej",
                        data_przejazdu=dzis,
                        status=StatusRezerwacji.WYBRANA,
                    )
                )
    koordynator = AGENCI["koordynator"](broker)
    for k, funkcja, operacja in koordynator.zadania_cykliczne():
        asyncio.run(koordynator.wykonaj(k, funkcja, operacja))
    assert status(trwa) == Status.W_TRAKCIE
    assert status(zakonczona) == Status.W_TRAKCIE

    for k, funkcja, operacja in koordynator.zadania_cykliczne():
        asyncio.run(koordynator.wykonaj(k, funkcja, operacja))
    assert status(zakonczona) == Status.DO_ROZLICZENIA


def test_brak_danych_transportu_jest_odnotowany_w_logu(broker):
    id_d = utworz(status=Status.ZATWIERDZONY)
    agent = AGENCI["transport"](broker)
    k = Komunikat(typ=Typ.ZADANIE_TRANSPORTU, id_delegacji=id_d)
    asyncio.run(agent.wykonaj(k, *agent.obslugi()[k.typ]))
    with sesja() as s:
        wpis = s.scalar(select(LogOperacji).where(LogOperacji.id_delegacji == id_d))
    assert wpis.id_agenta == "AG-04" and "S22IT1S0P2-27" in wpis.komunikat
