"""Regresja własnej trasy spoza seeda: Bydgoszcz, uzupełnienie i wybór ofert.

Adapter w pamięci nie wykonuje zewnętrznego I/O; korutyny dostarczamy do końca
bez systemowej pętli sieciowej. Testy obejmują rzeczywiste metody agentów,
transakcje, audyt oraz kontrolery formularzy; nie zastępują testów HTTP/ASGI.
"""

from datetime import date
from urllib.parse import urlencode
from uuid import uuid4

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import select
from starlette.requests import Request

from kwatermistrz.api.organizacja import przyjmij_wariant, przyjmij_wybor
from kwatermistrz.api.schemas import WniosekIn
from kwatermistrz.api.wnioski import zarejestruj_wniosek
from kwatermistrz.config import settings
from kwatermistrz.db import sesja
from kwatermistrz.lokalnie import BrokerLokalny
from kwatermistrz.messaging import Komunikat, Typ
from kwatermistrz.models import Delegacja, LogDecyzji, Polaczenie, Przejazd, StatusRezerwacji, Zolnierz
from kwatermistrz.podroze import BladPodrozy, WariantPodrozyIn, WyborPodrozy, sprawdz_decyzje, zestawienie
from kwatermistrz.sesje import CIASTECZKO, token_csrf, utworz_sesje
from kwatermistrz.web.strony import dane_wniosku, uzupelnij_wariant, widok_podrozy, wybierz_podroz


def zakoncz(korutyna):
    try:
        korutyna.send(None)
    except StopIteration as wynik:
        return wynik.value
    finally:
        korutyna.close()
    raise AssertionError("Adapter lokalny oczekuje zewnętrznego I/O — ten test wymaga zmiany")


def nowy_wniosek(broker, miejsce="bydgoszcz", data="2026-11-15", **zmiany):
    with sesja() as s:
        d = zakoncz(
            zarejestruj_wniosek(
                s,
                broker,
                WniosekIn(
                    **(
                        {
                            "id_zolnierza": "ZOL-003",
                            "id_przedsiewziecia": "PRZ-01",
                            "cel_wyjazdu": "Samodzielny test Bydgoszczy",
                            "miejsce_docelowe": miejsce,
                            "data_wyjazdu": data,
                            "data_powrotu": "2026-12-11",
                            "srodek_transportu": "kolej",
                            "potrzeba_zakwaterowania": False,
                        }
                        | zmiany
                    )
                ),
            )
        )
        ident = d.id_delegacji
    zakoncz(
        broker.publikuj(
            Komunikat(
                typ=Typ.DECYZJA_PRZELOZONEGO,
                id_delegacji=ident,
                dane={"id_osoby": "ZOL-001", "decyzja": "Zgoda"},
            )
        )
    )
    return ident


def oferta(kierunek="Tam", **pola):
    return WariantPodrozyIn(
        **(
            {
                "klucz": str(uuid4()),
                "kierunek": kierunek,
                "przewoznik": "Przewoźnik testowy",
                "srodek_transportu": "kolej",
                "godzina_odjazdu": "06:00" if kierunek == "Tam" else "18:00",
                "czas_min": 180,
                "koszt": "89.90",
                "liczba_przesiadek": 0,
                "punktualnosc": "91.50",
                "ocena_bezpieczenstwa": 4,
                "zrodlo": "Dane syntetyczne do testu, nie rzeczywista oferta przewoźnika",
            }
            | pola
        )
    )


def dodaj(broker, ident, wariant):
    with sesja() as s:
        zakoncz(przyjmij_wariant(s, broker, ident, s.get(Zolnierz, "ZOL-001"), wariant))


def rekomendowane(s, ident):
    return {
        "id_tam" if w["kierunek"] == "Tam" else "id_powrot": w["id_przejazdu"]
        for w in zestawienie(s, s.get(Delegacja, ident))["warianty"]
        if w["rekomendowany"]
    }


def request_form(data=None, csrf=True):
    klucz = settings.session_secret.get_secret_value()
    token = utworz_sesje("ZOL-001", klucz)
    fields = dict(data or {})
    if csrf:
        fields["csrf"] = token_csrf(token, klucz)
    body = urlencode(fields).encode()

    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}

    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/app/wnioski/test/podroz",
            "scheme": "http",
            "server": ("testserver", 80),
            "query_string": b"",
            "headers": [
                (b"content-type", b"application/x-www-form-urlencoded"),
                (b"cookie", f"{CIASTECZKO}={token}".encode()),
            ],
        },
        receive,
    ), fields.get("csrf", "")


def test_bydgoszcz_pokazuje_dalsze_dzialania_zamiast_pustych_tabel():
    broker = BrokerLokalny()
    ident = nowy_wniosek(broker)
    with sesja() as s:
        dane = zestawienie(s, s.get(Delegacja, ident))
        assert dane["warianty"] == [] and dane["brak_kierunkow"] == ["Tam", "Powrót"]
        assert dane["termin_rozpoczecia"] is None  # PRZ-01 dotyczy Torunia, nie Bydgoszczy
        html = widok_podrozy(ident, request_form()[0], s, s.get(Zolnierz, "ZOL-001")).body.decode()
        assert "Brak danych ofert dla tej trasy" in html and "Dodaj sprawdzoną ofertę" in html
        assert "Przekaż do uzgodnienia" in html
        assert "<table>" not in html and "Porównaj ponownie" not in html


def test_uzupelnienie_ofert_bydgoszczy_prowadzi_do_zapisu_wyboru_i_audytu():
    broker = BrokerLokalny()
    ident = nowy_wniosek(broker)
    tam, powrot = oferta(), oferta("Powrót")
    dodaj(broker, ident, tam)
    dodaj(broker, ident, powrot)
    with sesja() as s:
        dane = zestawienie(s, s.get(Delegacja, ident))
        assert dane["mozna_zatwierdzic"] and len(dane["warianty"]) == 2
        assert all(w["zrodlo"] and w["punktualnosc"] == 0.915 for w in dane["warianty"])
        assert {w["skad"] for w in dane["warianty"]} == {"Warszawa", "bydgoszcz"}
        zakoncz(przyjmij_wybor(s, broker, ident, s.get(Zolnierz, "ZOL-001"), WyborPodrozy(**rekomendowane(s, ident))))
    with sesja() as s:
        rows = list(s.scalars(select(Przejazd).where(Przejazd.id_delegacji == ident)))
        assert all(r.status == StatusRezerwacji.WYBRANA for r in rows)
        assert all(r.powod_zmiany for r in rows)
        logs = list(s.scalars(select(LogDecyzji).where(LogDecyzji.typ_decyzji == "Dodanie wariantu podróży")))
        assert len(logs) == 2 and all(wpis.decyzja_czlowieka == "Zatwierdzona" for wpis in logs)


def test_reczna_oferta_nie_jest_globalna_oferta_dla_innego_wniosku():
    broker = BrokerLokalny()
    pierwszy = nowy_wniosek(broker)
    dodaj(broker, pierwszy, oferta())
    drugi = nowy_wniosek(broker)
    with sesja() as s:
        assert zestawienie(s, s.get(Delegacja, drugi))["warianty"] == []


def test_ponowienie_dodania_nie_dubluje_oferty():
    broker = BrokerLokalny()
    ident = nowy_wniosek(broker)
    wariant = oferta()
    dodaj(broker, ident, wariant)
    dodaj(broker, ident, wariant)
    with sesja() as s:
        assert len(zestawienie(s, s.get(Delegacja, ident))["warianty"]) == 1


def test_dopasowanie_miasta_pomija_wielkosc_liter_i_nadmiarowe_spacje():
    broker = BrokerLokalny()
    ident = nowy_wniosek(broker, "  tOrUń  ", "2026-12-06")
    with sesja() as s:
        assert zestawienie(s, s.get(Delegacja, ident))["mozna_zatwierdzic"]


def test_brak_ofert_mozna_trwale_przekazac_do_uzgodnienia():
    broker = BrokerLokalny()
    ident = nowy_wniosek(broker)
    with sesja() as s:
        zakoncz(
            przyjmij_wybor(
                s,
                broker,
                ident,
                s.get(Zolnierz, "ZOL-001"),
                WyborPodrozy(
                    odrzuc_wszystkie=True,
                    uzasadnienie="Potrzebne oferty dla Bydgoszczy",
                ),
            )
        )
    with sesja() as s:
        osoba = s.get(Zolnierz, "ZOL-001")
        assert zestawienie(s, s.get(Delegacja, ident))["do_uzgodnienia"]
        assert not dane_wniosku(s, ident, osoba)["w_toku"]
        html = widok_podrozy(ident, request_form()[0], s, osoba).body.decode()
        assert "Sprawa została przekazana" in html and 'name="przewoznik"' not in html
        with pytest.raises(HTTPException) as e:
            zakoncz(przyjmij_wariant(s, broker, ident, osoba, oferta()))
        assert e.value.status_code == 409


@pytest.mark.parametrize(
    "pola",
    [
        {"koszt": "-1"},
        {"koszt": "1.001"},
        {"czas_min": 0},
        {"punktualnosc": 101},
        {"ocena_bezpieczenstwa": 6},
        {"zrodlo": ""},
        {"godzina_odjazdu": "25:00"},
    ],
)
def test_nieprawidlowe_oferty_sa_odrzucane(pola):
    with pytest.raises(ValidationError):
        oferta(**pola)


def test_blady_formularz_oferty_zachowuje_dane_i_pokazuje_blad_html():
    broker = BrokerLokalny()
    ident = nowy_wniosek(broker)
    fields = oferta().model_dump(mode="json") | {"koszt": "-1", "przewoznik": "Moja wpisana nazwa"}
    request, _ = request_form(fields)
    with sesja() as s:
        response = zakoncz(uzupelnij_wariant(ident, request, s=s, broker=broker, uzytkownik=s.get(Zolnierz, "ZOL-001")))
        html = response.body.decode()
        assert response.status_code == 422 and 'role="alert"' in html
        assert "Moja wpisana nazwa" in html and "koszt" in html
        assert not zestawienie(s, s.get(Delegacja, ident))["warianty"]


def test_formularz_oferty_zapisuje_wariant_i_przekierowuje():
    broker = BrokerLokalny()
    ident = nowy_wniosek(broker)
    request, _ = request_form(oferta().model_dump(mode="json"))
    with sesja() as s:
        response = zakoncz(uzupelnij_wariant(ident, request, s=s, broker=broker, uzytkownik=s.get(Zolnierz, "ZOL-001")))
        assert response.status_code == 303
    with sesja() as s:
        assert len(zestawienie(s, s.get(Delegacja, ident))["warianty"]) == 1


def test_formularz_wyboru_zachowuje_uzasadnienie_po_bledzie():
    broker = BrokerLokalny()
    ident = nowy_wniosek(broker)
    dodaj(broker, ident, oferta())
    request, csrf = request_form()
    with sesja() as s:
        response = zakoncz(
            wybierz_podroz(
                ident,
                request,
                id_tam="1",
                id_powrot="",
                akcja="wybierz",
                uzasadnienie="Moje uzasadnienie",
                csrf=csrf,
                s=s,
                broker=broker,
                uzytkownik=s.get(Zolnierz, "ZOL-001"),
            )
        )
        assert response.status_code == 422 and "Moje uzasadnienie" in response.body.decode()


def test_osoba_obca_i_brak_csrf_nie_pozwalaja_dodac_oferty():
    broker = BrokerLokalny()
    ident = nowy_wniosek(broker)
    with sesja() as s:
        with pytest.raises(HTTPException) as e:
            zakoncz(przyjmij_wariant(s, broker, ident, s.get(Zolnierz, "ZOL-003"), oferta()))
        assert e.value.status_code == 403
        request, _ = request_form(oferta().model_dump(mode="json"), csrf=False)
        with pytest.raises(HTTPException) as e:
            zakoncz(uzupelnij_wariant(ident, request, s=s, broker=broker, uzytkownik=s.get(Zolnierz, "ZOL-001")))
        assert e.value.status_code == 403


def test_zrodlo_wpisane_przez_uzytkownika_jest_escapowane_w_html():
    broker = BrokerLokalny()
    ident = nowy_wniosek(broker)
    dodaj(broker, ident, oferta(zrodlo="<script>alert(1)</script>"))
    with sesja() as s:
        html = widok_podrozy(ident, request_form()[0], s, s.get(Zolnierz, "ZOL-001")).body.decode()
        assert "<script>alert(1)</script>" not in html and "&lt;script&gt;" in html


def test_czas_przyjazdu_oferty_przez_polnoc_ma_wlasciwa_date():
    broker = BrokerLokalny()
    ident = nowy_wniosek(broker)
    dodaj(broker, ident, oferta(godzina_odjazdu="23:00", czas_min=180))
    with sesja() as s:
        dane = zestawienie(s, s.get(Delegacja, ident))
        assert dane["warianty"][0]["przyjazd"].date() == date(2026, 11, 16)
        assert s.get(Polaczenie, dane["warianty"][0]["id_polaczenia"]).godzina_przyjazdu.hour == 2


def test_formularz_wyboru_po_uzupelnieniu_obu_kierunkow_zapisuje_decyzje():
    broker = BrokerLokalny()
    ident = nowy_wniosek(broker)
    dodaj(broker, ident, oferta())
    dodaj(broker, ident, oferta("Powrót"))
    request, csrf = request_form()
    with sesja() as s:
        wybor = rekomendowane(s, ident)
        response = zakoncz(
            wybierz_podroz(
                ident,
                request,
                id_tam=str(wybor["id_tam"]),
                id_powrot=str(wybor["id_powrot"]),
                akcja="wybierz",
                kryterium="standardowe",
                uzasadnienie="",
                csrf=csrf,
                s=s,
                broker=broker,
                uzytkownik=s.get(Zolnierz, "ZOL-001"),
            )
        )
        assert response.status_code == 303
    with sesja() as s:
        html = widok_podrozy(ident, request, s, s.get(Zolnierz, "ZOL-001"), przekazano=True).body.decode()
        assert "Decyzja została zapisana" in html and "Decyzja przekazana do agenta" not in html


def test_agent_sam_odrzuca_nieuprawnione_dodanie_oferty():
    broker = BrokerLokalny()
    ident = nowy_wniosek(broker)
    zakoncz(
        broker.publikuj(
            Komunikat(
                typ=Typ.PROPOZYCJA_TRANSPORTU,
                id_delegacji=ident,
                dane={**oferta().model_dump(mode="json"), "id_osoby": "ZOL-004"},
            )
        )
    )
    with sesja() as s:
        assert not zestawienie(s, s.get(Delegacja, ident))["warianty"]


def test_krakow_z_innym_terminem_i_pojazdem_jednostki_nie_rekomenduje_pociagu():
    broker = BrokerLokalny()
    ident = nowy_wniosek(
        broker,
        "Kraków",
        id_przedsiewziecia="PRZ-03",
        data_powrotu="2026-11-19",
        srodek_transportu="pojazd służbowy",
    )
    with sesja() as s:
        dane = zestawienie(s, s.get(Delegacja, ident))
        assert dane["termin_rozpoczecia"] is None  # PRZ-03 zaczyna się dopiero 30.11
        assert len(dane["warianty"]) == 4
        assert not any(w["rekomendowany"] for w in dane["warianty"])
        assert dane["brak_preferowanego"] == ["Tam", "Powrót"]
        html = widok_podrozy(ident, request_form()[0], s, s.get(Zolnierz, "ZOL-001")).body.decode()
        assert "Dostępność pojazdu musi potwierdzić jednostka" in html
        assert "Rekomendowany:" not in html
        assert 'name="zgoda_na_zmiane_transportu"' in html


@pytest.mark.parametrize("zgoda,uzasadnienie", [(False, "Zgoda na kolej"), (True, ""), (False, "")])
def test_zmiana_transportu_wymaga_zarowno_zgody_jak_i_uzasadnienia(zgoda, uzasadnienie):
    broker = BrokerLokalny()
    ident = nowy_wniosek(broker, "Kraków", srodek_transportu="pojazd służbowy")
    with sesja() as s:
        d = s.get(Delegacja, ident)
        w = zestawienie(s, d)["warianty"]
        ids = {"id_tam" if r["kierunek"] == "Tam" else "id_powrot": r["id_przejazdu"] for r in w}
        with pytest.raises(BladPodrozy, match="zgodę na zmianę"):
            sprawdz_decyzje(s, d, WyborPodrozy(**ids, zgoda_na_zmiane_transportu=zgoda, uzasadnienie=uzasadnienie))


def test_swiadoma_zmiana_transportu_zapisuje_wybor_bez_bledu_obcego_terminu():
    broker = BrokerLokalny()
    ident = nowy_wniosek(
        broker,
        "Kraków",
        id_przedsiewziecia="PRZ-03",
        data_powrotu="2026-11-19",
        srodek_transportu="pojazd służbowy",
    )
    with sesja() as s:
        w = zestawienie(s, s.get(Delegacja, ident))["warianty"]
        ids = {"id_tam" if r["kierunek"] == "Tam" else "id_powrot": r["id_przejazdu"] for r in w}
        request, csrf = request_form()
        response = zakoncz(
            wybierz_podroz(
                ident,
                request,
                **{k: str(v) for k, v in ids.items()},
                akcja="wybierz",
                zgoda_na_zmiane_transportu=True,
                uzasadnienie="Jednostka uzgodniła zmianę na kolej",
                csrf=csrf,
                s=s,
                broker=broker,
                uzytkownik=s.get(Zolnierz, "ZOL-001"),
            )
        )
        assert response.status_code == 303
    with sesja() as s:
        rows = list(
            s.scalars(
                select(Przejazd).where(Przejazd.id_delegacji == ident, Przejazd.status == StatusRezerwacji.WYBRANA)
            )
        )
        assert len(rows) == 2
        wpis = s.scalar(select(LogDecyzji).where(LogDecyzji.typ_decyzji == "Zapis wyboru podróży"))
        assert "zgoda na zmianę transportu: True" in wpis.uzasadnienie


def test_pojazd_jednostki_ma_pierwszenstwo_przed_tansza_koleja():
    broker = BrokerLokalny()
    ident = nowy_wniosek(broker, "Toruń", "2026-12-06", srodek_transportu="pojazd służbowy")
    with sesja() as s:
        dane = zestawienie(s, s.get(Delegacja, ident))
        rekomendacje = [w for w in dane["warianty"] if w["rekomendowany"]]
        assert len(rekomendacje) == 2
        assert all(w["srodek_transportu"] == "pojazd służbowy" for w in rekomendacje)


def test_potwierdzenie_pojazdu_dla_krakowa_pozwala_wybrac_zadany_transport():
    broker = BrokerLokalny()
    ident = nowy_wniosek(
        broker,
        "Kraków",
        id_przedsiewziecia="PRZ-03",
        data_powrotu="2026-11-19",
        srodek_transportu="pojazd służbowy",
    )
    for kierunek in ("Tam", "Powrót"):
        dodaj(
            broker,
            ident,
            oferta(
                kierunek,
                srodek_transportu="pojazd służbowy",
                przewoznik="JW 1001",
                zrodlo="Testowe potwierdzenie dyspozytora pojazdów jednostki",
            ),
        )
    with sesja() as s:
        ids = rekomendowane(s, ident)
        assert len(ids) == 2
        zakoncz(przyjmij_wybor(s, broker, ident, s.get(Zolnierz, "ZOL-001"), WyborPodrozy(**ids)))
    with sesja() as s:
        wybrane = [w for w in zestawienie(s, s.get(Delegacja, ident))["warianty"] if w["status"] == "wybrana"]
        assert len(wybrane) == 2
        assert all(w["srodek_transportu"] == "pojazd służbowy" for w in wybrane)


def test_jeden_dzien_nie_rekomenduje_powrotu_przed_dojazdem_pojazdu():
    broker = BrokerLokalny()
    ident = nowy_wniosek(
        broker,
        "Toruń",
        "2026-11-16",
        data_powrotu="2026-11-16",
        srodek_transportu="pojazd służbowy",
    )
    with sesja() as s:
        d = s.get(Delegacja, ident)
        dane = zestawienie(s, d)
        assert not any(w["rekomendowany"] for w in dane["warianty"])
        assert dane["brak_spojnej_preferowanej_pary"]
        assert not any(r.rekomendowany for r in s.scalars(select(Przejazd)))
        html = widok_podrozy(ident, request_form()[0], s, s.get(Zolnierz, "ZOL-001")).body.decode()
        assert "nie ma zgodnej czasowo pary" in html


def test_rekomendowana_para_wybiera_pozniejszy_powrot_i_daje_sie_zapisac():
    broker = BrokerLokalny()
    ident = nowy_wniosek(broker, data_powrotu="2026-11-15")
    dodaj(broker, ident, oferta(godzina_odjazdu="08:00", czas_min=180))
    dodaj(broker, ident, oferta("Powrót", godzina_odjazdu="07:00", koszt="10.00"))
    with sesja() as s:
        dane = zestawienie(s, s.get(Delegacja, ident))
        assert dane["brak_spojnej_pary"] and not dane["mozna_zatwierdzic"]
        assert not any(w["rekomendowany"] for w in dane["warianty"])
    dodaj(broker, ident, oferta("Powrót", godzina_odjazdu="18:00", koszt="100.00"))
    with sesja() as s:
        d = s.get(Delegacja, ident)
        dane = zestawienie(s, d)
        wybrane = sprawdz_decyzje(s, d, WyborPodrozy(**rekomendowane(s, ident)))
        assert len(wybrane) == 2 and dane["mozna_zatwierdzic"]
        powrot = next(w for w in dane["warianty"] if w["rekomendowany"] and w["kierunek"] == "Powrót")
        assert powrot["godzina_odjazdu"].hour == 18
        zakoncz(przyjmij_wybor(s, broker, ident, s.get(Zolnierz, "ZOL-001"), WyborPodrozy(**rekomendowane(s, ident))))


def test_blad_czasu_wskazuje_konkretne_godziny_zamiast_ogolnego_komunikatu():
    broker = BrokerLokalny()
    ident = nowy_wniosek(broker, data_powrotu="2026-11-15")
    dodaj(broker, ident, oferta(godzina_odjazdu="16:00", czas_min=150))
    dodaj(broker, ident, oferta("Powrót", godzina_odjazdu="17:00"))
    with sesja() as s:
        d = s.get(Delegacja, ident)
        ids = {
            "id_tam" if w["kierunek"] == "Tam" else "id_powrot": w["id_przejazdu"]
            for w in zestawienie(s, d)["warianty"]
        }
        with pytest.raises(BladPodrozy, match="17:00.*18:30"):
            sprawdz_decyzje(s, d, WyborPodrozy(**ids))
