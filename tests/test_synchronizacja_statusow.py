"""S22IT1S0P2-23: synchronizacja statusu delegacji z faktami modułów."""

import asyncio
from datetime import date, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from pomoc_sesji import zaloguj_z_csrf
from sqlalchemy import func, select

from kwatermistrz.agents.koordynator import KONTROLA_STATUSU, FaktyStatusu, Koordynator, kwalifikuj_status
from kwatermistrz.api.main import app, get_broker
from kwatermistrz.db import sesja, teraz
from kwatermistrz.messaging import Komunikat, Typ
from kwatermistrz.models import (
    Delegacja,
    Jednostka,
    LogDecyzji,
    LogOperacji,
    PolecenieWyjazdu,
    Przejazd,
    Rozliczenie,
    Status,
    StatusRezerwacji,
    StatusRozliczenia,
    Zakwaterowanie,
    Zolnierz,
)

DZIS = date(2026, 11, 25)


@pytest.fixture
def klient(broker):
    app.dependency_overrides[get_broker] = lambda: broker
    with TestClient(app, follow_redirects=False) as c:
        yield c
    app.dependency_overrides.clear()


def fakty(status: str, **zmiany) -> FaktyStatusu:
    dane = {
        "id_delegacji": "DEL-SYNC",
        "status": status,
        "data_wyjazdu": DZIS + timedelta(days=2),
        "data_powrotu": DZIS + timedelta(days=4),
        "ma_polecenie": False,
        "wybrane_kierunki": (),
        "potrzeba_zakwaterowania": False,
        "ma_zakwaterowanie": False,
        "status_rozliczenia": None,
    }
    return FaktyStatusu(**(dane | zmiany))


def kompletna_organizacja(status: str = Status.ZATWIERDZONY, **zmiany) -> FaktyStatusu:
    return fakty(status, ma_polecenie=True, wybrane_kierunki=("Powrót", "Tam"), **zmiany)


def test_zatwierdzona_delegacja_przechodzi_do_zorganizowanej_tylko_z_kompletem_faktow():
    wynik = kwalifikuj_status(kompletna_organizacja(), DZIS)
    assert wynik.nowy_status == Status.ZORGANIZOWANY
    assert not wynik.rozbieznosc

    oczekiwanie = kwalifikuj_status(fakty(Status.ZATWIERDZONY, ma_polecenie=True), DZIS)
    assert oczekiwanie.nowy_status is None and not oczekiwanie.rozbieznosc

    z_noclegiem = kompletna_organizacja(potrzeba_zakwaterowania=True)
    assert kwalifikuj_status(z_noclegiem, DZIS).nowy_status is None
    assert (
        kwalifikuj_status(kompletna_organizacja(potrzeba_zakwaterowania=True, ma_zakwaterowanie=True), DZIS).nowy_status
        == Status.ZORGANIZOWANY
    )


@pytest.mark.parametrize(
    ("data_wyjazdu", "oczekiwany"),
    [(DZIS, Status.W_TRAKCIE), (DZIS - timedelta(days=1), Status.W_TRAKCIE), (DZIS + timedelta(days=1), None)],
)
def test_zorganizowana_delegacja_przechodzi_w_trakcie_na_granicy_daty(data_wyjazdu, oczekiwany):
    wynik = kwalifikuj_status(kompletna_organizacja(Status.ZORGANIZOWANY, data_wyjazdu=data_wyjazdu), DZIS)
    assert wynik.nowy_status == oczekiwany


@pytest.mark.parametrize(
    ("data_powrotu", "oczekiwany"),
    [(DZIS - timedelta(days=1), Status.DO_ROZLICZENIA), (DZIS, None), (DZIS + timedelta(days=1), None)],
)
def test_delegacja_w_trakcie_przechodzi_do_rozliczenia_dopiero_po_dacie_powrotu(data_powrotu, oczekiwany):
    wynik = kwalifikuj_status(
        kompletna_organizacja(
            Status.W_TRAKCIE,
            data_wyjazdu=DZIS - timedelta(days=3),
            data_powrotu=data_powrotu,
        ),
        DZIS,
    )
    assert wynik.nowy_status == oczekiwany


@pytest.mark.parametrize(
    "status_rozliczenia",
    [StatusRozliczenia.PRZEKAZANE, StatusRozliczenia.PRZEKAZANE_Z_ZASTRZEZENIAMI],
)
def test_delegacja_do_rozliczenia_przechodzi_do_rozliczonej_po_przekazaniu(status_rozliczenia):
    wynik = kwalifikuj_status(
        kompletna_organizacja(
            Status.DO_ROZLICZENIA,
            data_wyjazdu=None,
            data_powrotu=None,
            status_rozliczenia=status_rozliczenia,
        ),
        DZIS,
    )
    assert wynik.nowy_status == Status.ROZLICZONY


def test_jeden_przebieg_przesuwa_najwyzej_o_jeden_krok():
    wynik = kwalifikuj_status(
        kompletna_organizacja(
            data_wyjazdu=DZIS - timedelta(days=5),
            data_powrotu=DZIS - timedelta(days=1),
            status_rozliczenia=StatusRozliczenia.PRZEKAZANE,
        ),
        DZIS,
    )
    assert wynik.nowy_status == Status.ZORGANIZOWANY


@pytest.mark.parametrize("status", [Status.ODRZUCONY, Status.ROZLICZONY])
def test_statusy_koncowe_nigdy_nie_sa_automatycznie_zmieniane(status):
    wynik = kwalifikuj_status(kompletna_organizacja(status, status_rozliczenia=StatusRozliczenia.PRZEKAZANE), DZIS)
    assert wynik.nowy_status is None


@pytest.mark.parametrize("status", [Status.ZLOZONY, Status.W_AKCEPTACJI, Status.DO_UZUPELNIENIA])
def test_fakty_organizacji_nie_zastepuja_decyzji_przelozonego(status):
    wynik = kwalifikuj_status(kompletna_organizacja(status), DZIS)
    assert wynik.nowy_status is None and wynik.rozbieznosc


def test_brakujace_i_sprzeczne_fakty_sa_rozrozniane_od_normalnego_oczekiwania():
    oczekiwanie_na_organizacje = kwalifikuj_status(fakty(Status.ZATWIERDZONY), DZIS)
    assert oczekiwanie_na_organizacje.nowy_status is None and not oczekiwanie_na_organizacje.rozbieznosc

    sprzeczna_organizacja = kwalifikuj_status(fakty(Status.ZORGANIZOWANY), DZIS)
    assert sprzeczna_organizacja.nowy_status is None and sprzeczna_organizacja.rozbieznosc

    brak_daty = kwalifikuj_status(
        kompletna_organizacja(Status.W_TRAKCIE, data_wyjazdu=DZIS - timedelta(days=1), data_powrotu=None), DZIS
    )
    assert brak_daty.nowy_status is None and brak_daty.rozbieznosc

    oczekiwanie_na_rozliczenie = kwalifikuj_status(
        kompletna_organizacja(
            Status.DO_ROZLICZENIA,
            data_wyjazdu=DZIS - timedelta(days=4),
            data_powrotu=DZIS - timedelta(days=1),
        ),
        DZIS,
    )
    assert oczekiwanie_na_rozliczenie.nowy_status is None and not oczekiwanie_na_rozliczenie.rozbieznosc


# ---------------------------------------------------------------- wykonanie przez Koordynatora


def utworz_delegacje(status: str, **pola) -> str:
    domyslne = {
        "id_delegacji": "DEL-SYNC",
        "id_zolnierza": "ZOL-003",
        "dane_wniosku": {},
        "status": status,
        "data_wyjazdu": teraz().date() + timedelta(days=2),
        "data_powrotu": teraz().date() + timedelta(days=4),
        "potrzeba_zakwaterowania": False,
        "szacowany_koszt": Decimal("345.67"),
    }
    with sesja() as s:
        s.add(Delegacja(**(domyslne | pola)))
    return domyslne["id_delegacji"]


def dodaj_kompletna_organizacje(id_delegacji: str, z_noclegiem: bool = False) -> None:
    dzis = teraz().date()
    with sesja() as s:
        s.add(PolecenieWyjazdu(numer=f"PW/{id_delegacji}", id_delegacji=id_delegacji, wersja_wzoru="2026-1", tresc={}))
        for kierunek in ("Tam", "Powrót"):
            s.add(
                Przejazd(
                    id_delegacji=id_delegacji,
                    kierunek=kierunek,
                    srodek_transportu="kolej",
                    data_przejazdu=dzis,
                    status=StatusRezerwacji.WYBRANA,
                )
            )
        if z_noclegiem:
            s.add(
                Zakwaterowanie(
                    id_delegacji=id_delegacji,
                    id_obiektu="OBJ-01",
                    data_od=dzis,
                    data_do=dzis,
                    cena_za_dobe=Decimal("100"),
                    koszt_calkowity=Decimal("100"),
                    status=StatusRezerwacji.WYBRANA,
                )
            )


def dodaj_rozliczenie(id_delegacji: str, status: str) -> None:
    with sesja() as s:
        s.add(
            Rozliczenie(
                id_delegacji=id_delegacji,
                status=status,
                kwota_naleznosci=Decimal("123.45"),
            )
        )


def wykonaj_kontrole(broker, id_delegacji: str = "DEL-SYNC"):
    koordynator = Koordynator(broker)
    komunikat = Komunikat(typ=KONTROLA_STATUSU, id_delegacji=id_delegacji)
    return asyncio.run(
        koordynator.wykonaj(komunikat, koordynator.synchronizuj_status, "Synchronizacja statusu delegacji")
    )


def wykonaj_cykle(broker, ile: int = 3) -> None:
    koordynator = Koordynator(broker)
    for _ in range(ile):
        for komunikat, funkcja, operacja in koordynator.zadania_cykliczne():
            asyncio.run(koordynator.wykonaj(komunikat, funkcja, operacja))


def liczba_operacji(id_delegacji: str) -> int:
    with sesja() as s:
        return s.scalar(select(func.count()).select_from(LogOperacji).where(LogOperacji.id_delegacji == id_delegacji))


@pytest.mark.parametrize(
    ("status_przed", "status_po", "data_wyjazdu", "data_powrotu", "status_rozliczenia"),
    [
        (Status.ZATWIERDZONY, Status.ZORGANIZOWANY, 2, 4, None),
        (Status.ZORGANIZOWANY, Status.W_TRAKCIE, 0, 2, None),
        (Status.W_TRAKCIE, Status.DO_ROZLICZENIA, -3, -1, None),
        (Status.DO_ROZLICZENIA, Status.ROZLICZONY, -5, -1, StatusRozliczenia.PRZEKAZANE),
    ],
)
def test_koordynator_wykonuje_kazde_dozwolone_przejscie_i_audytuje_decyzje(
    broker, status_przed, status_po, data_wyjazdu, data_powrotu, status_rozliczenia
):
    dzis = teraz().date()
    id_d = utworz_delegacje(
        status_przed,
        data_wyjazdu=dzis + timedelta(days=data_wyjazdu),
        data_powrotu=dzis + timedelta(days=data_powrotu),
    )
    dodaj_kompletna_organizacje(id_d)
    if status_rozliczenia:
        dodaj_rozliczenie(id_d, status_rozliczenia)
    with sesja() as s:
        budzet_przed = s.get(Jednostka, "JW-1001").budzet_wykorzystany

    wynik = wykonaj_kontrole(broker, id_d)

    with sesja() as s:
        d = s.get(Delegacja, id_d)
        decyzja = s.scalar(select(LogDecyzji).where(LogDecyzji.typ_decyzji == "Synchronizacja statusu delegacji"))
        assert d.status == status_po
        assert d.szacowany_koszt == Decimal("345.67")
        assert s.get(Jednostka, "JW-1001").budzet_wykorzystany == budzet_przed
        if status_rozliczenia:
            r = s.scalar(select(Rozliczenie).where(Rozliczenie.id_delegacji == id_d))
            assert r.status == status_rozliczenia and r.kwota_naleznosci == Decimal("123.45")
    assert wynik.wynik == "Sukces"
    assert decyzja.wybrany_wariant == f"{status_przed} → {status_po}"
    assert status_przed in decyzja.uzasadnienie and status_po in decyzja.uzasadnienie


def test_powtorny_przebieg_jest_idempotentny_i_nie_przeskakuje_kolejnego_etapu(broker):
    dzis = teraz().date()
    id_d = utworz_delegacje(
        Status.ZATWIERDZONY,
        data_wyjazdu=dzis - timedelta(days=5),
        data_powrotu=dzis - timedelta(days=1),
    )
    dodaj_kompletna_organizacje(id_d)
    wykonaj_kontrole(broker, id_d)
    with sesja() as s:
        assert s.get(Delegacja, id_d).status == Status.ZORGANIZOWANY
    wykonaj_kontrole(broker, id_d)
    with sesja() as s:
        assert s.get(Delegacja, id_d).status == Status.W_TRAKCIE
        assert s.scalar(select(func.count()).select_from(LogDecyzji)) == 2


def test_rozbieznosc_przed_zatwierdzeniem_powiadamia_tylko_przelozonego(broker):
    id_d = utworz_delegacje(Status.ZLOZONY)
    with sesja() as s:
        s.add(PolecenieWyjazdu(numer="PW/SPRZECZNY", id_delegacji=id_d, wersja_wzoru="2026-1", tresc={}))

    wynik = wykonaj_kontrole(broker, id_d)

    powiadomienia = [k for k in broker.wyslane if k.typ == Typ.POWIADOMIENIE]
    assert [k.dane["do"] for k in powiadomienia] == ["ZOL-001"]
    assert wynik.wynik == "Ostrzeżenie"
    with sesja() as s:
        assert s.get(Delegacja, id_d).status == Status.ZLOZONY
        operacja = s.scalar(select(LogOperacji).where(LogOperacji.id_delegacji == id_d))
        decyzja = s.scalar(select(LogDecyzji).where(LogDecyzji.id_operacji == operacja.id_operacji))
    assert id_d in operacja.komunikat and Status.ZLOZONY in operacja.komunikat and "polecenie" in operacja.komunikat
    assert decyzja.typ_decyzji == "Rozbieżność statusu delegacji"


def test_rozbieznosc_organizacji_powiadamia_przelozonego_i_wnioskodawce(broker):
    id_d = utworz_delegacje(Status.ZORGANIZOWANY)
    wykonaj_kontrole(broker, id_d)
    odbiorcy = [k.dane["do"] for k in broker.wyslane if k.typ == Typ.POWIADOMIENIE]
    assert odbiorcy == ["ZOL-001", "ZOL-003"]


def test_brak_przelozonego_jest_jawny_i_nie_tworzony_jest_fikcyjny_odbiorca(broker):
    id_d = utworz_delegacje(Status.ZORGANIZOWANY)
    with sesja() as s:
        s.get(Zolnierz, "ZOL-003").id_przelozonego = None

    wykonaj_kontrole(broker, id_d)

    odbiorcy = [k.dane["do"] for k in broker.wyslane if k.typ == Typ.POWIADOMIENIE]
    assert odbiorcy == ["ZOL-003"]
    with sesja() as s:
        operacja = s.scalar(select(LogOperacji).where(LogOperacji.id_delegacji == id_d))
    assert "brak przełożonego" in operacja.komunikat


def test_identyczny_odcisk_nie_powtarza_powiadomien_a_zmieniony_stan_moze_eskalowac_ponownie(broker):
    id_d = utworz_delegacje(Status.ZORGANIZOWANY)
    wykonaj_kontrole(broker, id_d)
    pierwsza_liczba = len([k for k in broker.wyslane if k.typ == Typ.POWIADOMIENIE])
    wykonaj_kontrole(broker, id_d)
    assert len([k for k in broker.wyslane if k.typ == Typ.POWIADOMIENIE]) == pierwsza_liczba

    with sesja() as s:
        s.add(PolecenieWyjazdu(numer="PW/ZMIANA", id_delegacji=id_d, wersja_wzoru="2026-1", tresc={}))
    wykonaj_kontrole(broker, id_d)

    assert len([k for k in broker.wyslane if k.typ == Typ.POWIADOMIENIE]) == pierwsza_liczba * 2
    with sesja() as s:
        assert (
            s.scalar(
                select(func.count())
                .select_from(LogDecyzji)
                .where(LogDecyzji.typ_decyzji == "Rozbieżność statusu delegacji")
            )
            == 2
        )


def test_cykl_nie_loguje_delegacji_bez_zmiany_i_bez_rozbieznosci(broker):
    id_d = utworz_delegacje(Status.ZATWIERDZONY)

    wykonaj_cykle(broker)

    assert liczba_operacji(id_d) == 0


def test_cykl_loguje_i_eskaluje_nowa_rozbieznosc_tylko_raz(broker):
    id_d = utworz_delegacje(Status.ZORGANIZOWANY)

    wykonaj_cykle(broker)

    assert liczba_operacji(id_d) == 1
    powiadomienia = [k for k in broker.wyslane if k.typ == Typ.POWIADOMIENIE]
    assert [k.dane["do"] for k in powiadomienia] == ["ZOL-001", "ZOL-003"]


@pytest.mark.parametrize("status", [Status.ODRZUCONY, Status.ROZLICZONY])
def test_cykl_pomija_statusy_koncowe_nawet_gdy_fakty_sa_sprzeczne(broker, status):
    id_d = utworz_delegacje(status)
    if status == Status.ODRZUCONY:
        with sesja() as s:
            s.add(PolecenieWyjazdu(numer="PW/KONCOWY", id_delegacji=id_d, wersja_wzoru="2026-1", tresc={}))

    wykonaj_cykle(broker)

    assert liczba_operacji(id_d) == 0


# ---------------------------------------------------------------- zgłoszenie w interfejsie


def test_wnioskodawca_moze_poprosic_o_sprawdzenie_i_widzi_najnowszy_status_z_czasem(klient):
    id_d = utworz_delegacje(Status.ZATWIERDZONY)
    dodaj_kompletna_organizacje(id_d)
    assert zaloguj_z_csrf(klient, "ZOL-003").status_code == 303

    strona = klient.get(f"/app/wnioski/{id_d}")
    assert f'action="/app/wnioski/{id_d}/sprawdz-status"' in strona.text
    assert "Poproś o sprawdzenie statusu" in strona.text
    assert "Na tym etapie od" in strona.text

    odpowiedz = klient.post(f"/app/wnioski/{id_d}/sprawdz-status")
    assert odpowiedz.status_code == 303
    assert odpowiedz.headers["location"] == f"/app/wnioski/{id_d}?wynik=status-zmieniony"

    wynik = klient.get(odpowiedz.headers["location"])
    assert wynik.status_code == 200
    assert "Status został sprawdzony i bezpiecznie zaktualizowany" in wynik.text
    assert Status.ZORGANIZOWANY in wynik.text
    with sesja() as s:
        assert s.get(Delegacja, id_d).status == Status.ZORGANIZOWANY


def test_zgloszona_rozbieznosc_pokazuje_ze_sprawa_wymaga_wyjasnienia(klient):
    id_d = utworz_delegacje(Status.ZORGANIZOWANY)
    zaloguj_z_csrf(klient, "ZOL-003")

    odpowiedz = klient.post(f"/app/wnioski/{id_d}/sprawdz-status")
    assert odpowiedz.status_code == 303
    wynik = klient.get(odpowiedz.headers["location"])
    assert "Status wymaga wyjaśnienia przez człowieka" in wynik.text
    assert "dane delegacji pozostały bez zmian" in wynik.text


def test_sprawdzenie_statusu_wymaga_csrf_i_wlasnosci_delegacji(klient):
    id_d = utworz_delegacje(Status.ZATWIERDZONY, id_zolnierza="ZOL-004")
    zaloguj_z_csrf(klient, "ZOL-003")
    assert klient.post(f"/app/wnioski/{id_d}/sprawdz-status").status_code == 404

    klient.headers.pop("X-CSRF-Token")
    assert klient.post(f"/app/wnioski/{id_d}/sprawdz-status").status_code == 403
