"""Dane początkowe: słownik agentów i przykładowa jednostka (dane fikcyjne, jak w modelu w Excelu)."""

from datetime import date
from decimal import Decimal

from sqlalchemy.orm import Session

from .models import Agent, Jednostka, ObiektZakwaterowania, Przedsiewziecie, Zolnierz

AGENCI = [
    (
        "AG-01",
        "Koordynator",
        "Prowadzi delegację przez etapy, deleguje zadania, rozstrzyga konflikty",
        "Mieszany",
        "orch-0.1",
    ),
    ("AG-02", "Agent Wniosków", "Przyjmuje zgłoszenie, waliduje i uzupełnia dane wniosku", "Mieszany", "walidacja-1.0"),
    (
        "AG-03",
        "Agent Zatwierdzeń",
        "Kieruje wniosek do przełożonego, przypomina, eskaluje do zastępcy",
        "Regułowy",
        "rules-appr-1.0",
    ),
    (
        "AG-04",
        "Agent Transportu",
        "Dobiera środek transportu, łączy przejazdy grupowe",
        "Probabilistyczny",
        "w-budowie",
    ),
    (
        "AG-05",
        "Agent Zakwaterowania",
        "Szuka miejsc w obiektach wojskowych, potem cywilnych w limicie",
        "Mieszany",
        "w-budowie",
    ),
    ("AG-06", "Agent Rozliczeń", "Wylicza diety i zwroty, odczytuje rachunki", "Mieszany", "w-budowie"),
    ("AG-07", "Agent Zgodności", "Kontroluje limity, budżet i dokumenty", "Regułowy", "w-budowie"),
    (
        "AG-08",
        "Agent Monitorujący",
        "Śledzi zmiany w trakcie wyjazdu i uruchamia replanowanie",
        "Probabilistyczny",
        "w-budowie",
    ),
]


def zaladuj_dane_poczatkowe(s: Session) -> None:
    if s.get(Agent, "AG-01") is not None:
        return
    s.add_all(
        Agent(id_agenta=a[0], nazwa=a[1], odpowiedzialnosc=a[2], sposob_decydowania=a[3], wersja_modelu=a[4])
        for a in AGENCI
    )
    s.add(
        Jednostka(
            id_jednostki="JW-1001",
            nazwa="JW 1001 – Batalion Logistyczny",
            miejscowosc="Warszawa",
            budzet_delegacji=Decimal("250000"),
        )
    )
    s.flush()
    # Dowódca i zastępca wskazują na siebie nawzajem – zastępcę dowódcy ustawiamy dopiero, gdy obaj są w bazie
    dowodca = Zolnierz(
        id_zolnierza="ZOL-001",
        stopien="ppłk",
        imie_nazwisko="Michał Stępień",
        id_jednostki="JW-1001",
        pododdzial="dowództwo",
        funkcja="Dowódca jednostki",
    )
    s.add(dowodca)
    s.flush()
    s.add(
        Zolnierz(
            id_zolnierza="ZOL-002",
            stopien="mjr",
            imie_nazwisko="Robert Tomaszewski",
            id_jednostki="JW-1001",
            pododdzial="dowództwo",
            funkcja="Zastępca dowódcy",
            id_przelozonego="ZOL-001",
        )
    )
    s.flush()
    dowodca.id_zastepcy = "ZOL-002"
    s.flush()
    s.add_all(
        [
            Zolnierz(
                id_zolnierza="ZOL-003",
                stopien="por.",
                imie_nazwisko="Magdalena Szewczyk",
                id_jednostki="JW-1001",
                pododdzial="1 kompania zaopatrzenia",
                funkcja="Magazynier",
                id_przelozonego="ZOL-001",
                id_zastepcy="ZOL-002",
            ),
            Zolnierz(
                id_zolnierza="ZOL-004",
                stopien="kpr.",
                imie_nazwisko="Bartosz Wójcik",
                id_jednostki="JW-1001",
                pododdzial="2 kompania transportowa",
                funkcja="Kierowca",
                id_przelozonego="ZOL-001",
                id_zastepcy="ZOL-002",
            ),
            Przedsiewziecie(
                id_przedsiewziecia="PRZ-01",
                nazwa="Szkolenie z logistyki wojskowej",
                miejscowosc="Toruń",
                data_rozpoczecia=date(2026, 12, 7),
                data_zakonczenia=date(2026, 12, 11),
            ),
            ObiektZakwaterowania(
                id_obiektu="OBJ-01",
                nazwa="Internat wojskowy Toruń",
                rodzaj="Wojskowy",
                miejscowosc="Toruń",
                cena_za_dobe=Decimal("55"),
            ),
            ObiektZakwaterowania(
                id_obiektu="OBJ-03",
                nazwa="Hotel Kopernik",
                rodzaj="Cywilny",
                miejscowosc="Toruń",
                cena_za_dobe=Decimal("260"),
            ),
        ]
    )
