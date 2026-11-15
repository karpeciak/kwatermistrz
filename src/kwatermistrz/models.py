"""Model danych – odwzorowanie modelu informacyjnego (Kwatermistrz_model_informacyjny.xlsx).

Logi: LogDecyzji wskazuje wyłącznie na LogOperacji (decyzja zapada w ramach operacji, a operacja
zna agenta i delegację). Dzięki temu w modelu nie ma cyklu Agent → LogOperacji → Delegacja → LogDecyzji → Agent.
"""

from datetime import date, datetime, time
from decimal import Decimal

from sqlalchemy import JSON, Boolean, Date, DateTime, Float, ForeignKey, Integer, Numeric, String, Text, Time
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base, teraz


class Status:
    """Etapy obsługi wniosku – nazwy z potrzeb informacyjnych S22IT1S0P2-32."""

    ROBOCZY = "roboczy"  # niezłożony wniosek (S22IT1S0P2-37)
    ZLOZONY = "złożony"
    DO_UZUPELNIENIA = "do uzupełnienia"
    W_AKCEPTACJI = "w akceptacji"
    ZATWIERDZONY = "zatwierdzony"
    ODRZUCONY = "odrzucony"
    ZORGANIZOWANY = "zorganizowany"
    W_TRAKCIE = "w trakcie"
    DO_ROZLICZENIA = "do rozliczenia"
    ROZLICZONY = "rozliczony"


class StatusRezerwacji:
    """Stan wariantu przejazdu (S22IT1S0P2-27) i zakwaterowania (S22IT1S0P2-39)."""

    PROPOZYCJA = "propozycja"
    WYBRANA = "wybrana"
    DO_DECYZJI_PRZELOZONEGO = "do decyzji przełożonego"  # wariant wymagający odstępstwa (-39)
    DO_UZGODNIENIA = "do uzgodnienia"  # odrzucono wszystkie warianty (-27)


class StatusRozliczenia:
    W_PRZYGOTOWANIU = "w przygotowaniu"
    GOTOWE_DO_WERYFIKACJI = "gotowe do weryfikacji"  # S22IT1S0P2-36
    ZWERYFIKOWANE = "zweryfikowane"  # S22IT1S0P2-43
    PRZEKAZANE = "przekazane do komórki finansowej"
    PRZEKAZANE_Z_ZASTRZEZENIAMI = "przekazane z zastrzeżeniami"


# ---------------------------------------------------------------- wymiary


class Jednostka(Base):
    __tablename__ = "jednostka"

    id_jednostki: Mapped[str] = mapped_column(String(20), primary_key=True)
    nazwa: Mapped[str] = mapped_column(String(200))
    miejscowosc: Mapped[str] = mapped_column(String(100))
    budzet_delegacji: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0)
    # Kwota zaangażowana w zatwierdzone delegacje – dostępny budżet dla S22IT1S0P2-35
    budzet_wykorzystany: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0, server_default="0")
    # Kwota wydatkowana – należności z zatwierdzonych rozliczeń (S22IT1S0P2-36, podzadanie 66)
    budzet_wydatkowany: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0, server_default="0")


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
    # Godzina rozpoczęcia zajęć – ocena terminowości dojazdu (S22IT1S0P2-27, -39)
    godzina_rozpoczecia: Mapped[time | None] = mapped_column(Time)


class ObiektZakwaterowania(Base):
    __tablename__ = "obiekt_zakwaterowania"

    id_obiektu: Mapped[str] = mapped_column(String(20), primary_key=True)
    nazwa: Mapped[str] = mapped_column(String(200))
    rodzaj: Mapped[str] = mapped_column(String(20))  # Wojskowy / Cywilny
    miejscowosc: Mapped[str] = mapped_column(String(100))
    cena_za_dobe: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    # S22IT1S0P2-39: dostępność miejsc i czas dojazdu do miejsca zadania w tej miejscowości
    liczba_wolnych_miejsc: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    czas_dojazdu_min: Mapped[int | None] = mapped_column(Integer)


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
    srodek_transportu: Mapped[str | None] = mapped_column(String(50))  # preferowany transport
    potrzeba_zakwaterowania: Mapped[bool] = mapped_column(Boolean, default=True, server_default="1")
    # Podstawa wyjazdu: rozkaz (S22IT1S0P2-33, -38)
    podstawa_numer_rozkazu: Mapped[str | None] = mapped_column(String(50))
    podstawa_data_rozkazu: Mapped[date | None] = mapped_column(Date)
    szacowany_koszt: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))  # S22IT1S0P2-35
    data_zlozenia: Mapped[datetime | None] = mapped_column(DateTime)  # pusta dla wniosku roboczego
    status: Mapped[str] = mapped_column(String(40), default=Status.ZLOZONY)
    data_zmiany_statusu: Mapped[datetime] = mapped_column(DateTime, default=teraz)  # czas oczekiwania (-32)
    # Wniosek roboczy (S22IT1S0P2-37)
    etap_przygotowania: Mapped[str | None] = mapped_column(String(30))
    data_ostatniej_zmiany: Mapped[datetime | None] = mapped_column(DateTime)
    # Zatwierdzanie (S22IT1S0P2-13, -14)
    id_zatwierdzajacego: Mapped[str | None] = mapped_column(ForeignKey("zolnierz.id_zolnierza"))
    termin_decyzji: Mapped[datetime | None] = mapped_column(DateTime)
    liczba_przypomnien: Mapped[int] = mapped_column(Integer, default=0)
    przekazano_zastepcy: Mapped[bool] = mapped_column(Boolean, default=False)
    decyzja: Mapped[str | None] = mapped_column(String(20))
    data_decyzji: Mapped[datetime | None] = mapped_column(DateTime)
    uzasadnienie_decyzji: Mapped[str | None] = mapped_column(Text)  # odrzucenie / do uzupełnienia (-35)
    # Bezpieczne ponowienie zapisu wniosku (S22IT1S0P2-22)
    klucz_idempotencji: Mapped[str | None] = mapped_column(String(64), unique=True)

    def ustaw_status(self, status: str) -> None:
        """Zmienia etap obsługi i zapamiętuje, od kiedy wniosek na nim czeka (S22IT1S0P2-32)."""
        if status != self.status:
            self.status = status
            self.data_zmiany_statusu = teraz()


class PolecenieWyjazdu(Base):
    """Polecenie wyjazdu służbowego sporządzone po zatwierdzeniu wniosku (S22IT1S0P2-38)."""

    __tablename__ = "polecenie_wyjazdu"

    numer: Mapped[str] = mapped_column(String(30), primary_key=True)  # np. PW/2026/0001
    id_delegacji: Mapped[str] = mapped_column(ForeignKey("delegacja.id_delegacji"), unique=True)
    data_sporzadzenia: Mapped[datetime] = mapped_column(DateTime, default=teraz)
    wersja_wzoru: Mapped[str] = mapped_column(String(20))
    tresc: Mapped[dict] = mapped_column(JSON)  # dane wypełnione według wzoru


class Polaczenie(Base):
    """Oferta przejazdu (rozkład przewoźnika, plan transportu) – dane do porównania tras (S22IT1S0P2-27)."""

    __tablename__ = "polaczenie"

    id_polaczenia: Mapped[str] = mapped_column(String(20), primary_key=True)
    skad: Mapped[str] = mapped_column(String(100))
    dokad: Mapped[str] = mapped_column(String(100))
    srodek_transportu: Mapped[str] = mapped_column(String(40))  # kolej / transport wojskowy / pojazd służbowy
    przewoznik: Mapped[str] = mapped_column(String(100))
    godzina_odjazdu: Mapped[time] = mapped_column(Time)
    godzina_przyjazdu: Mapped[time] = mapped_column(Time)
    czas_min: Mapped[int] = mapped_column(Integer)
    liczba_przesiadek: Mapped[int] = mapped_column(Integer, default=0)
    koszt: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    punktualnosc: Mapped[float] = mapped_column(Float)  # odsetek punktualnych kursów 0–1
    ocena_bezpieczenstwa: Mapped[int] = mapped_column(Integer)  # 1–5


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
    # Wybór wariantu podróży (S22IT1S0P2-27)
    id_polaczenia: Mapped[str | None] = mapped_column(ForeignKey("polaczenie.id_polaczenia"))
    rekomendowany: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    uzasadnienie: Mapped[str | None] = mapped_column(Text)


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
    # S22IT1S0P2-39: czas dojazdu z obiektu i wariant wymagający odstępstwa od limitu
    czas_dojazdu_min: Mapped[int | None] = mapped_column(Integer)
    wymaga_odstepstwa: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")


class Rozliczenie(Base):
    """Rozliczenie delegacji: dane od delegowanego i wyliczenie należności (S22IT1S0P2-36, -43)."""

    __tablename__ = "rozliczenie"

    id_rozliczenia: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    id_delegacji: Mapped[str] = mapped_column(ForeignKey("delegacja.id_delegacji"), unique=True)
    status: Mapped[str] = mapped_column(String(40), default=StatusRozliczenia.W_PRZYGOTOWANIU)
    data_zlozenia: Mapped[datetime] = mapped_column(DateTime, default=teraz)
    # Dane od delegowanego
    faktyczny_wyjazd: Mapped[datetime | None] = mapped_column(DateTime)
    faktyczny_powrot: Mapped[datetime | None] = mapped_column(DateTime)
    zapewnione_posilki: Mapped[dict] = mapped_column(JSON, default=dict)  # {"śniadanie": 3, "obiad": 2}
    bezplatne_zakwaterowanie: Mapped[bool] = mapped_column(Boolean, default=False)
    # Wyliczenie należności
    dieta: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    zwrot_przejazdu: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    zwrot_noclegu: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    ryczalty: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    kwota_naleznosci: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    # Weryfikacja i przekazanie (S22IT1S0P2-43)
    zastrzezenia: Mapped[str | None] = mapped_column(Text)
    data_przekazania: Mapped[datetime | None] = mapped_column(DateTime)


class DokumentKosztowy(Base):
    """Dokument kosztowy przypisany do rozliczenia (S22IT1S0P2-36), bez duplikatów (S22IT1S0P2-20)."""

    __tablename__ = "dokument_kosztowy"

    id_dokumentu: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    id_rozliczenia: Mapped[int] = mapped_column(ForeignKey("rozliczenie.id_rozliczenia"), index=True)
    rodzaj: Mapped[str] = mapped_column(String(30))  # bilet / rachunek za nocleg / faktura / inny
    numer: Mapped[str] = mapped_column(String(60))
    data: Mapped[date] = mapped_column(Date)
    kwota: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    kwota_uznana: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    przekracza_limit: Mapped[bool] = mapped_column(Boolean, default=False)
    # Identyfikacja ponownie przesłanego dokumentu: numer|data|kwota (S22IT1S0P2-20)
    klucz_unikalnosci: Mapped[str] = mapped_column(String(200), unique=True)
    data_przekazania: Mapped[datetime] = mapped_column(DateTime, default=teraz)


class KorektaRozliczenia(Base):
    """Korekta wprowadzona podczas weryfikacji wyliczenia (S22IT1S0P2-43)."""

    __tablename__ = "korekta_rozliczenia"

    id_korekty: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    id_rozliczenia: Mapped[int] = mapped_column(ForeignKey("rozliczenie.id_rozliczenia"), index=True)
    pozycja: Mapped[str] = mapped_column(String(60))
    wartosc_przed: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    wartosc_po: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    powod: Mapped[str] = mapped_column(Text)
    znacznik_czasu: Mapped[datetime] = mapped_column(DateTime, default=teraz)


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
