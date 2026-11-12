"""Model danych – odwzorowanie modelu informacyjnego (Kwatermistrz_model_informacyjny.xlsx).

Logi: LogDecyzji wskazuje wyłącznie na LogOperacji (decyzja zapada w ramach operacji, a operacja
zna agenta i delegację). Dzięki temu w modelu nie ma cyklu Agent → LogOperacji → Delegacja → LogDecyzji → Agent.
"""

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import JSON, Boolean, Date, DateTime, Float, ForeignKey, Integer, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base, teraz


class Status:
    ZLOZONY = "Złożony"
    W_WALIDACJI = "W walidacji"
    DO_POPRAWY = "Do poprawy"
    OCZEKUJE = "Oczekuje na akceptację"
    ZATWIERDZONA = "Zatwierdzona"
    ODRZUCONA = "Odrzucona"
    W_REALIZACJI = "W realizacji"
    ROZLICZONA = "Rozliczona"


# ---------------------------------------------------------------- wymiary


class Jednostka(Base):
    __tablename__ = "jednostka"

    id_jednostki: Mapped[str] = mapped_column(String(20), primary_key=True)
    nazwa: Mapped[str] = mapped_column(String(200))
    miejscowosc: Mapped[str] = mapped_column(String(100))
    budzet_delegacji: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0)


class Zolnierz(Base):
    __tablename__ = "zolnierz"

    id_zolnierza: Mapped[str] = mapped_column(String(20), primary_key=True)
    stopien: Mapped[str] = mapped_column(String(30))
    imie_nazwisko: Mapped[str] = mapped_column(String(120))
    id_jednostki: Mapped[str] = mapped_column(ForeignKey("jednostka.id_jednostki"))
    pododdzial: Mapped[str | None] = mapped_column(String(100))
    funkcja: Mapped[str | None] = mapped_column(String(100))
    # Struktura podległości (S22IT1S0P2-13)
    id_przelozonego: Mapped[str | None] = mapped_column(ForeignKey("zolnierz.id_zolnierza"))
    id_zastepcy: Mapped[str | None] = mapped_column(ForeignKey("zolnierz.id_zolnierza"))


class Przedsiewziecie(Base):
    __tablename__ = "przedsiewziecie"

    id_przedsiewziecia: Mapped[str] = mapped_column(String(20), primary_key=True)
    nazwa: Mapped[str] = mapped_column(String(200))
    miejscowosc: Mapped[str] = mapped_column(String(100))
    data_rozpoczecia: Mapped[date] = mapped_column(Date)
    data_zakonczenia: Mapped[date] = mapped_column(Date)
    pierwotna_data_zakonczenia: Mapped[date | None] = mapped_column(Date)


class ObiektZakwaterowania(Base):
    __tablename__ = "obiekt_zakwaterowania"

    id_obiektu: Mapped[str] = mapped_column(String(20), primary_key=True)
    nazwa: Mapped[str] = mapped_column(String(200))
    rodzaj: Mapped[str] = mapped_column(String(20))  # Wojskowy / Cywilny
    miejscowosc: Mapped[str] = mapped_column(String(100))
    cena_za_dobe: Mapped[Decimal] = mapped_column(Numeric(10, 2))


class Agent(Base):
    __tablename__ = "agent"

    id_agenta: Mapped[str] = mapped_column(String(10), primary_key=True)
    nazwa: Mapped[str] = mapped_column(String(60))
    odpowiedzialnosc: Mapped[str] = mapped_column(String(300))
    sposob_decydowania: Mapped[str] = mapped_column(String(30))  # Regułowy / Probabilistyczny / Mieszany
    wersja_modelu: Mapped[str] = mapped_column(String(40))


# ---------------------------------------------------------------- proces


class Delegacja(Base):
    __tablename__ = "delegacja"

    id_delegacji: Mapped[str] = mapped_column(String(30), primary_key=True)
    id_zolnierza: Mapped[str] = mapped_column(ForeignKey("zolnierz.id_zolnierza"))
    id_przedsiewziecia: Mapped[str | None] = mapped_column(ForeignKey("przedsiewziecie.id_przedsiewziecia"))
    # Wniosek w postaci przesłanej przez użytkownika – pola typowane uzupełnia Agent Wniosków po walidacji
    dane_wniosku: Mapped[dict] = mapped_column(JSON, default=dict)
    cel_wyjazdu: Mapped[str | None] = mapped_column(Text)
    miejsce_docelowe: Mapped[str | None] = mapped_column(String(200))
    data_wyjazdu: Mapped[date | None] = mapped_column(Date)
    data_powrotu: Mapped[date | None] = mapped_column(Date)
    pierwotna_data_powrotu: Mapped[date | None] = mapped_column(Date)
    srodek_transportu: Mapped[str | None] = mapped_column(String(50))
    data_zlozenia: Mapped[datetime] = mapped_column(DateTime, default=teraz)
    status: Mapped[str] = mapped_column(String(40), default=Status.ZLOZONY)
    # Zatwierdzanie (S22IT1S0P2-13, -14)
    id_zatwierdzajacego: Mapped[str | None] = mapped_column(ForeignKey("zolnierz.id_zolnierza"))
    termin_decyzji: Mapped[datetime | None] = mapped_column(DateTime)
    liczba_przypomnien: Mapped[int] = mapped_column(Integer, default=0)
    przekazano_zastepcy: Mapped[bool] = mapped_column(Boolean, default=False)
    decyzja: Mapped[str | None] = mapped_column(String(20))
    data_decyzji: Mapped[datetime | None] = mapped_column(DateTime)
    # Bezpieczne ponowienie zapisu wniosku (S22IT1S0P2-22)
    klucz_idempotencji: Mapped[str | None] = mapped_column(String(64), unique=True)


class Przejazd(Base):
    __tablename__ = "przejazd"

    id_przejazdu: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    id_delegacji: Mapped[str] = mapped_column(ForeignKey("delegacja.id_delegacji"))
    kierunek: Mapped[str] = mapped_column(String(10))  # Tam / Powrót
    srodek_transportu: Mapped[str] = mapped_column(String(40))
    numer_kursu: Mapped[str | None] = mapped_column(String(40))
    data_przejazdu: Mapped[date] = mapped_column(Date)
    id_grupy_przejazdu: Mapped[str | None] = mapped_column(String(30))
    koszt_indywidualny: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    koszt: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    status: Mapped[str] = mapped_column(String(20))
    powod_zmiany: Mapped[str | None] = mapped_column(Text)


class Zakwaterowanie(Base):
    __tablename__ = "zakwaterowanie"

    id_zakwaterowania: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    id_delegacji: Mapped[str] = mapped_column(ForeignKey("delegacja.id_delegacji"))
    id_obiektu: Mapped[str] = mapped_column(ForeignKey("obiekt_zakwaterowania.id_obiektu"))
    data_od: Mapped[date] = mapped_column(Date)
    data_do: Mapped[date] = mapped_column(Date)
    cena_za_dobe: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    koszt_calkowity: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    status: Mapped[str] = mapped_column(String(30))
    uzasadnienie: Mapped[str | None] = mapped_column(Text)


class Rozliczenie(Base):
    __tablename__ = "rozliczenie"

    id_rozliczenia: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    id_delegacji: Mapped[str] = mapped_column(ForeignKey("delegacja.id_delegacji"), unique=True)
    data_zlozenia: Mapped[date] = mapped_column(Date)
    liczba_dob: Mapped[int] = mapped_column(Integer)
    kwota_diet: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    koszt_przejazdow: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    koszt_noclegow: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    limit_noclegow: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    przekroczenie_limitu: Mapped[bool] = mapped_column(Boolean, default=False)
    uzasadnienie: Mapped[str | None] = mapped_column(Text)
    decyzja_przelozonego: Mapped[str | None] = mapped_column(String(20))
    noclegi_uznane: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    kwota_naleznosci: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    status: Mapped[str] = mapped_column(String(40))


# ---------------------------------------------------------------- logi agentów


class LogOperacji(Base):
    """Każda czynność agenta: co zrobił, z jakim wynikiem, ile trwało, która to próba."""

    __tablename__ = "log_operacji"

    id_operacji: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    znacznik_czasu: Mapped[datetime] = mapped_column(DateTime, default=teraz, index=True)
    id_korelacji: Mapped[str | None] = mapped_column(String(50), index=True)
    id_delegacji: Mapped[str | None] = mapped_column(ForeignKey("delegacja.id_delegacji"), index=True)
    id_agenta: Mapped[str] = mapped_column(ForeignKey("agent.id_agenta"))
    etap: Mapped[str] = mapped_column(String(40))
    operacja: Mapped[str] = mapped_column(String(120))
    wynik: Mapped[str] = mapped_column(String(30))  # Sukces / Ostrzeżenie / Błąd
    czas_trwania_ms: Mapped[int] = mapped_column(Integer)
    nr_proby: Mapped[int] = mapped_column(Integer, default=1)
    komunikat: Mapped[str | None] = mapped_column(Text)
    id_komunikatu: Mapped[str | None] = mapped_column(String(40))


class LogDecyzji(Base):
    """Decyzja agenta – także niedeterministyczna: warianty, wybór, uzasadnienie, pewność, wersja modelu."""

    __tablename__ = "log_decyzji"

    id_decyzji: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    znacznik_czasu: Mapped[datetime] = mapped_column(DateTime, default=teraz)
    id_operacji: Mapped[int] = mapped_column(ForeignKey("log_operacji.id_operacji"), index=True)
    typ_decyzji: Mapped[str] = mapped_column(String(80))
    rodzaj_decyzji: Mapped[str] = mapped_column(String(20))  # Regułowa / Probabilistyczna
    liczba_wariantow: Mapped[int] = mapped_column(Integer, default=1)
    wybrany_wariant: Mapped[str] = mapped_column(Text)
    uzasadnienie: Mapped[str] = mapped_column(Text)
    pewnosc: Mapped[float] = mapped_column(Float, default=1.0)
    wersja_modelu: Mapped[str] = mapped_column(String(40))
    wymaga_zatwierdzenia: Mapped[bool] = mapped_column(Boolean, default=False)
    decyzja_czlowieka: Mapped[str | None] = mapped_column(String(20))  # Zatwierdzona / Odrzucona
    id_weryfikujacego: Mapped[str | None] = mapped_column(String(20))
