"""Dane początkowe i słownikowe (fikcyjne, zgodne z modelem informacyjnym).

Ładowane przy każdym starcie API: brakujące rekordy są dodawane, istniejące – aktualizowane do wartości
z tego pliku (to źródło prawdy dla danych słownikowych). Nie dotyka danych procesu (delegacje, logi)
ani pól zmienianych przez system (np. `Jednostka.budzet_wykorzystany`).
"""

from datetime import date, time
from decimal import Decimal

from sqlalchemy.orm import Session

from .db import Base
from .models import Agent, Jednostka, ObiektZakwaterowania, Polaczenie, Przedsiewziecie, Zolnierz

AGENCI = [
    dict(
        id_agenta="AG-01",
        nazwa="Koordynator",
        sposob_decydowania="Mieszany",
        wersja_modelu="orch-0.2",
        odpowiedzialnosc="Prowadzi delegację przez etapy, deleguje zadania, rozstrzyga konflikty",
    ),
    dict(
        id_agenta="AG-02",
        nazwa="Agent Wniosków",
        sposob_decydowania="Mieszany",
        wersja_modelu="walidacja-1.0",
        odpowiedzialnosc="Przyjmuje i waliduje wniosek, sporządza polecenie wyjazdu",
    ),
    dict(
        id_agenta="AG-03",
        nazwa="Agent Zatwierdzeń",
        sposob_decydowania="Regułowy",
        wersja_modelu="rules-appr-1.0",
        odpowiedzialnosc="Kieruje wniosek do przełożonego, przypomina, eskaluje do zastępcy",
    ),
    dict(
        id_agenta="AG-04",
        nazwa="Agent Transportu",
        sposob_decydowania="Probabilistyczny",
        wersja_modelu="trasy-0.1",
        odpowiedzialnosc="Porównuje warianty podróży i rekomenduje przejazd",
    ),
    dict(
        id_agenta="AG-05",
        nazwa="Agent Zakwaterowania",
        sposob_decydowania="Mieszany",
        wersja_modelu="nocleg-0.1",
        odpowiedzialnosc="Dobiera zakwaterowanie w limicie z terminowym dojazdem na miejsce zadania",
    ),
    dict(
        id_agenta="AG-06",
        nazwa="Agent Rozliczeń",
        sposob_decydowania="Mieszany",
        wersja_modelu="naleznosci-0.1",
        odpowiedzialnosc="Wylicza należności na podstawie dokumentów kosztowych",
    ),
    dict(
        id_agenta="AG-07",
        nazwa="Agent Zgodności",
        sposob_decydowania="Regułowy",
        wersja_modelu="weryfikacja-0.1",
        odpowiedzialnosc="Weryfikuje wyliczenie ze stawkami i limitami, przekazuje rozliczenie do finansów",
    ),
    dict(
        id_agenta="AG-08",
        nazwa="Agent Monitorujący",
        sposob_decydowania="Probabilistyczny",
        wersja_modelu="w-budowie",
        odpowiedzialnosc="Śledzi zmiany w trakcie wyjazdu i uruchamia replanowanie",
    ),
]

JEDNOSTKI = [
    dict(
        id_jednostki="JW-1001",
        nazwa="JW 1001 – Batalion Logistyczny",
        miejscowosc="Warszawa",
        budzet_delegacji=Decimal("250000"),
    ),
]

_JW = dict(id_jednostki="JW-1001")
_PODWLADNY = dict(id_przelozonego="ZOL-001", id_zastepcy="ZOL-002", **_JW)
ZOLNIERZE = [
    # Dowódca i zastępca wskazują na siebie nawzajem – zastępcę dowódcy ustawia `zaladuj_dane_poczatkowe` na końcu
    dict(
        id_zolnierza="ZOL-001",
        stopien="ppłk",
        imie_nazwisko="Michał Stępień",
        pododdzial="dowództwo",
        funkcja="Dowódca jednostki",
        **_JW,
    ),
    dict(
        id_zolnierza="ZOL-002",
        stopien="mjr",
        imie_nazwisko="Robert Tomaszewski",
        pododdzial="dowództwo",
        funkcja="Zastępca dowódcy",
        id_przelozonego="ZOL-001",
        **_JW,
    ),
    dict(
        id_zolnierza="ZOL-003",
        stopien="por.",
        imie_nazwisko="Magdalena Szewczyk",
        pododdzial="1 kompania zaopatrzenia",
        funkcja="Magazynier",
        **_PODWLADNY,
    ),
    dict(
        id_zolnierza="ZOL-004",
        stopien="kpr.",
        imie_nazwisko="Bartosz Wójcik",
        pododdzial="2 kompania transportowa",
        funkcja="Kierowca",
        **_PODWLADNY,
    ),
    dict(
        id_zolnierza="ZOL-005",
        stopien="sierż.",
        imie_nazwisko="Anna Nowak",
        pododdzial="1 kompania zaopatrzenia",
        funkcja="Dowódca drużyny",
        **_PODWLADNY,
    ),
    dict(
        id_zolnierza="ZOL-006",
        stopien="st. szer.",
        imie_nazwisko="Paweł Dąbrowski",
        pododdzial="pluton remontowy",
        funkcja="Mechanik",
        **_PODWLADNY,
    ),
]

PRZEDSIEWZIECIA = [
    dict(
        id_przedsiewziecia="PRZ-01",
        nazwa="Szkolenie z logistyki wojskowej",
        miejscowosc="Toruń",
        data_rozpoczecia=date(2026, 12, 7),
        data_zakonczenia=date(2026, 12, 11),
        godzina_rozpoczecia=time(8, 0),
    ),
    dict(
        id_przedsiewziecia="PRZ-02",
        nazwa="Kurs łączności satelitarnej",
        miejscowosc="Zegrze",
        data_rozpoczecia=date(2026, 12, 14),
        data_zakonczenia=date(2026, 12, 18),
        godzina_rozpoczecia=time(9, 0),
    ),
    dict(
        id_przedsiewziecia="PRZ-03",
        nazwa="Odprawa logistyczna",
        miejscowosc="Kraków",
        data_rozpoczecia=date(2026, 11, 30),
        data_zakonczenia=date(2026, 12, 1),
        godzina_rozpoczecia=time(10, 0),
    ),
]


def _obiekt(id_, nazwa, rodzaj, miejscowosc, cena, wolne, dojazd):
    return dict(
        id_obiektu=id_,
        nazwa=nazwa,
        rodzaj=rodzaj,
        miejscowosc=miejscowosc,
        cena_za_dobe=Decimal(cena),
        liczba_wolnych_miejsc=wolne,
        czas_dojazdu_min=dojazd,
    )


OBIEKTY = [
    _obiekt("OBJ-01", "Internat wojskowy Toruń", "Wojskowy", "Toruń", "55", 0, 10),  # brak miejsc
    _obiekt("OBJ-02", "Hotel garnizonowy Chełmno", "Wojskowy", "Toruń", "60", 12, 45),
    _obiekt("OBJ-03", "Hotel Kopernik", "Cywilny", "Toruń", "260", 30, 15),
    _obiekt("OBJ-04", "Hotel Bulwar", "Cywilny", "Toruń", "340", 20, 5),  # ponad limit
    _obiekt("OBJ-05", "Pensjonat Pod Lipami", "Cywilny", "Toruń", "180", 6, 70),  # tani, ale daleko
    _obiekt("OBJ-06", "Hotel garnizonowy Zegrze", "Wojskowy", "Zegrze", "65", 15, 5),
    _obiekt("OBJ-07", "Internat wojskowy Kraków", "Wojskowy", "Kraków", "60", 0, 15),  # brak miejsc
    _obiekt("OBJ-08", "Hotel Wawel Business", "Cywilny", "Kraków", "290", 10, 20),
]


def _polaczenie(id_, dokad, srodek, przewoznik, odjazd, przyjazd, czas, przesiadki, koszt, punkt, bezp):
    return dict(
        id_polaczenia=id_,
        skad="Warszawa",
        dokad=dokad,
        srodek_transportu=srodek,
        przewoznik=przewoznik,
        godzina_odjazdu=time(*odjazd),
        godzina_przyjazdu=time(*przyjazd),
        czas_min=czas,
        liczba_przesiadek=przesiadki,
        koszt=Decimal(koszt),
        punktualnosc=punkt,
        ocena_bezpieczenstwa=bezp,
    )


POLACZENIA = [
    _polaczenie("POL-01", "Toruń", "kolej", "PKP Intercity (IC)", (6, 10), (8, 45), 155, 0, "89.00", 0.92, 5),
    _polaczenie("POL-02", "Toruń", "kolej", "PKP Intercity (TLK)", (16, 20), (19, 5), 165, 0, "55.00", 0.85, 5),
    _polaczenie("POL-03", "Toruń", "kolej", "Polregio przez Kutno", (15, 5), (18, 40), 215, 1, "47.00", 0.78, 4),
    _polaczenie(
        "POL-04",
        "Toruń",
        "transport wojskowy",
        "JW 1001 – pluton transportowy",
        (15, 0),
        (17, 50),
        170,
        0,
        "110.00",
        0.97,
        5,
    ),
    _polaczenie("POL-05", "Toruń", "pojazd służbowy", "JW 1001", (16, 0), (18, 30), 150, 0, "140.00", 0.95, 4),
    _polaczenie("POL-06", "Zegrze", "kolej", "Koleje Mazowieckie + autobus", (6, 30), (7, 45), 75, 1, "18.00", 0.80, 4),
    _polaczenie("POL-07", "Zegrze", "pojazd służbowy", "JW 1001", (7, 0), (7, 50), 50, 0, "45.00", 0.95, 4),
    _polaczenie("POL-08", "Kraków", "kolej", "PKP Intercity (EIP)", (5, 50), (8, 10), 140, 0, "169.00", 0.94, 5),
    _polaczenie("POL-09", "Kraków", "kolej", "PKP Intercity (IC)", (6, 5), (9, 10), 185, 0, "99.00", 0.88, 5),
]


def _uzupelnij(s: Session, model: type[Base], rekordy: list[dict]) -> None:
    """Dodaje brakujące rekordy, istniejące aktualizuje do wartości z listy."""
    for rekord in rekordy:
        klucz = rekord[model.__mapper__.primary_key[0].name]
        obiekt = s.get(model, klucz)
        if obiekt is None:
            s.add(model(**rekord))
        else:
            for pole, wartosc in rekord.items():
                setattr(obiekt, pole, wartosc)
        s.flush()


def zaladuj_dane_poczatkowe(s: Session) -> None:
    _uzupelnij(s, Agent, AGENCI)
    _uzupelnij(s, Jednostka, JEDNOSTKI)
    _uzupelnij(s, Zolnierz, ZOLNIERZE)
    s.get(Zolnierz, "ZOL-001").id_zastepcy = "ZOL-002"
    _uzupelnij(s, Przedsiewziecie, PRZEDSIEWZIECIA)
    _uzupelnij(s, ObiektZakwaterowania, OBIEKTY)
    _uzupelnij(s, Polaczenie, POLACZENIA)
    s.flush()
