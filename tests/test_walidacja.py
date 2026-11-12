from kwatermistrz.walidacja import waliduj_wniosek

POPRAWNY = {
    "cel_wyjazdu": "Szkolenie z logistyki",
    "miejsce_docelowe": "Toruń",
    "data_wyjazdu": "2026-12-07",
    "data_powrotu": "2026-12-11",
    "srodek_transportu": "pociąg",
}


def bledy(**zmiany):
    return {b.pole: b for b in waliduj_wniosek({**POPRAWNY, **zmiany}, jednostka="JW-1001")}


def test_poprawny_wniosek():
    assert bledy() == {}


def test_brak_celu_wyjazdu_s7():
    assert bledy(cel_wyjazdu="")["cel_wyjazdu"].komunikat == "Uzupełnij cel wyjazdu"


def test_data_powrotu_przed_wyjazdem_s8():
    assert "data_powrotu" in bledy(data_powrotu="2026-12-01")


def test_wyjazd_i_powrot_tego_samego_dnia_dopuszczalny_s8():
    assert bledy(data_powrotu="2026-12-07") == {}


def test_brak_jednostki_s9():
    wynik = waliduj_wniosek(POPRAWNY, jednostka=None)
    assert [b.historyjka for b in wynik] == ["S22IT1S0P2-9"]


def test_nieprawidlowy_format_daty_nie_porownuje_dat_s10():
    wynik = bledy(data_wyjazdu="7 grudnia")
    assert list(wynik) == ["data_wyjazdu"]
    assert "format" in wynik["data_wyjazdu"].komunikat


def test_format_polski_daty_akceptowany_s10():
    assert bledy(data_wyjazdu="07.12.2026") == {}


def test_miejsce_docelowe_z_samych_spacji_s11():
    assert "miejsce_docelowe" in bledy(miejsce_docelowe="   ")


def test_samochod_prywatny_wymaga_dodatkowych_danych_s12():
    wynik = bledy(srodek_transportu="samochód prywatny")
    assert set(wynik) == {"dane_transportu.nr_rejestracyjny", "dane_transportu.pojemnosc_silnika"}
    uzupelnione = bledy(
        srodek_transportu="samochód prywatny",
        dane_transportu={"nr_rejestracyjny": "WA 12345", "pojemnosc_silnika": "1600"},
    )
    assert uzupelnione == {}
