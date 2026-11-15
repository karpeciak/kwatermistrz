"""Integracja formularzy po scaleniu main i czytelność widocznej treści.

Kontrolery i lokalny broker działają bez zewnętrznego I/O. To test integracji
logiki i renderowania HTML; nie zastępuje testu HTTP ani silnika przeglądarki.
"""

from html.parser import HTMLParser

import pytest
from pomoc_brokera import BrokerTestowy
from test_oferty_transportu import nowy_wniosek, rekomendowane, request_form, zakoncz

from kwatermistrz.api.zatwierdzenia import decyzja_zbiorcza
from kwatermistrz.db import sesja
from kwatermistrz.models import Delegacja, Jednostka, Status, Zolnierz
from kwatermistrz.podroze import WyborPodrozy
from kwatermistrz.web.strony import (
    dane_wniosku,
    podsumowanie_wniosku,
    przyjmij_wybor,
    strona,
    widok_podrozy,
    zloz_nowy_wniosek,
)
from kwatermistrz.web.strony import (
    nowy_wniosek as formularz,
)


class WidocznyTekst(HTMLParser):
    """Pomija hidden i zawartość zamkniętych details poza summary."""

    def __init__(self, html):
        super().__init__()
        self.stack = []
        self.tekst = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        hidden = bool(self.stack and self.stack[-1][1]) or "hidden" in attrs or tag in {"script", "style"}
        if self.stack and self.stack[-1][0] == "details" and self.stack[-1][2] and tag != "summary":
            hidden = True
        if tag not in {"input", "br", "hr", "meta", "link", "img"}:
            self.stack.append((tag, hidden, tag == "details" and "open" not in attrs))

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i][0] == tag:
                self.stack = self.stack[:i]
                break

    def handle_data(self, data):
        if self.stack and self.stack[-1][0] == "details" and self.stack[-1][2]:
            return
        if not self.stack or not self.stack[-1][1]:
            self.tekst.append(data.strip())

    def __str__(self):
        return " ".join(x for x in self.tekst if x)


@pytest.mark.parametrize(
    "miejsce,transport", [("Bydgoszcz", "kolej"), ("Kraków", "pojazd służbowy"), ("Toruń", "kolej")]
)
def test_podroz_ma_jeden_glowny_komunikat_i_ukryte_szczegoly(miejsce, transport):
    broker = BrokerTestowy()
    ident = nowy_wniosek(broker, miejsce, srodek_transportu=transport)
    with sesja() as s:
        html = widok_podrozy(ident, request_form()[0], s, s.get(Zolnierz, "ZOL-001")).body.decode()
    widoczne = str(WidocznyTekst(html))
    assert html.count("data-komunikat-glowny") == 1
    assert "Dane przewoźników nie są pobierane" not in widoczne
    assert "Najpierw sprawdzamy" not in widoczne
    assert "Źródło:" not in widoczne
    assert "Przekaż do uzgodnienia" in widoczne or "Odrzuć wszystkie" in widoczne
    if miejsce == "Kraków":
        assert "PKP Intercity" not in widoczne
        assert "Inne środki transportu" in widoczne


def test_blad_zastepuje_komunikat_stanu_i_zachowuje_wpisane_dane():
    broker = BrokerTestowy()
    ident = nowy_wniosek(broker)
    with sesja() as s:
        html = widok_podrozy(
            ident,
            request_form()[0],
            s,
            s.get(Zolnierz, "ZOL-001"),
            blad="Wybierz oba przejazdy",
            dane_wyboru={"uzasadnienie": "Moje dane"},
        ).body.decode()
    widoczne = str(WidocznyTekst(html))
    assert "Wybierz oba przejazdy" in widoczne
    assert "Brak danych ofert dla tej trasy" not in widoczne
    assert "Moje dane" in widoczne
    assert html.count('role="alert"') == 1


def test_formularz_powrot_z_podsumowania_zachowuje_miasto_transport_i_wyszukiwanie():
    request = request_form()[0]
    with sesja() as s:
        osoba = s.get(Zolnierz, "ZOL-003")
        html = formularz(request, s, osoba).body.decode()
        assert 'id="miejsce-docelowe"' in html and 'hx-get="/app/miejscowosci"' in html
        assert "Pojazd z jednostki" in html
        dane = pola_wniosku() | {"miejsce_docelowe": "Bydgoszcz", "srodek_transportu": "pojazd służbowy"}
        response = podsumowanie_wniosku(request, popraw=True, s=s, uzytkownik=osoba, **dane)
        html = response.body.decode()
        assert 'value="Bydgoszcz"' in html
        assert 'value="pojazd służbowy" selected' in html


def pola_wniosku():
    return dict(
        id_przedsiewziecia="PRZ-02",
        cel_wyjazdu="Szkolenie łączności",
        miejsce_docelowe="Zegrze",
        data_wyjazdu="2026-12-14",
        data_powrotu="2026-12-18",
        podstawa_numer_rozkazu="R 1/2026",
        podstawa_data_rozkazu="2026-11-15",
        srodek_transportu="kolej",
        potrzeba_zakwaterowania=False,
        nr_rejestracyjny="",
        pojemnosc_silnika="",
    )


def test_podsumowanie_zlozenie_zatwierdzenie_budzet_i_wybor_po_scaleniu_main():
    broker = BrokerTestowy()
    request = request_form()[0]
    pola = pola_wniosku()
    with sesja() as s:
        osoba = s.get(Zolnierz, "ZOL-003")
        html = podsumowanie_wniosku(request, popraw=False, s=s, uzytkownik=osoba, **pola).body.decode()
        assert "Podsumowanie wniosku" in html and "Złóż wniosek" in html
        odpowiedz = zakoncz(zloz_nowy_wniosek(s=s, broker=broker, uzytkownik=osoba, **pola))
        ident = odpowiedz.headers["location"].rsplit("/", 1)[-1]
    with sesja() as s:
        assert s.get(Delegacja, ident).status == Status.W_AKCEPTACJI
        wynik = zakoncz(decyzja_zbiorcza(s, broker, s.get(Zolnierz, "ZOL-001"), [ident], "Zgoda"))
        assert wynik.przekazane == [ident] and not wynik.blad
    with sesja() as s:
        osoba = s.get(Zolnierz, "ZOL-001")
        d = s.get(Delegacja, ident)
        assert d.status == Status.ZATWIERDZONY
        assert s.get(Jednostka, "JW-1001").budzet_wykorzystany > 0
        html = widok_podrozy(ident, request, s, osoba).body.decode()
        assert html.count("Rekomendowany:") == 2
        zakoncz(przyjmij_wybor(s, broker, ident, osoba, WyborPodrozy(**rekomendowane(s, ident))))
    with sesja() as s:
        osoba = s.get(Zolnierz, "ZOL-003")
        stan = dane_wniosku(s, ident, osoba)
        assert not stan["w_toku"] and not stan["nieaktualny"]
        html = strona(request, s, "wniosek.html", osoba, **stan).body.decode()
        widoczne = str(WidocznyTekst(html))
        assert "Transport został wybrany" in widoczne and "Historia obsługi" in widoczne
        assert "Rekomendacja wariantu podróży" not in widoczne


def test_oczekiwanie_na_wybor_czlowieka_nie_jest_nieaktualnym_statusem():
    from datetime import timedelta

    from kwatermistrz.db import teraz

    broker = BrokerTestowy()
    ident = nowy_wniosek(broker, "Toruń", "2026-12-06")
    with sesja() as s:
        d = s.get(Delegacja, ident)
        d.data_zmiany_statusu = teraz() - timedelta(days=1)
        s.flush()
        stan = dane_wniosku(s, ident, s.get(Zolnierz, "ZOL-001"))
        assert not stan["w_toku"] and not stan["nieaktualny"]
