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
    # S22IT1S0P2-17: odwołanie przejazdu przez przewoźnika i przejazdy zastępcze
    ODWOLANA = "odwołana"  # zarezerwowany przejazd odwołany przez przewoźnika
    ALTERNATYWA = "alternatywa"  # przejazd zastępczy czekający na wybór delegowanego
    ANULOWANA = "anulowana"  # rezerwacja zastąpiona innym przejazdem albo niewybrana alternatywa
    # S22IT1S0P2-34: zmiana usługi w trakcie podróży zgłoszona przez dostawcę
    ZAKLOCONA = "zakłócona"  # zarezerwowana usługa objęta zmianą albo odwołaniem
    ZASTEPCZA = "zastępcza"  # rozwiązanie zastępcze czekające na akceptację delegowanego
    # S22IT1S0P2-40: wybrany obiekt przekracza limit noclegowy – rezerwacja wstrzymana do wyboru obiektu w limicie
    WSTRZYMANA = "wstrzymana – ponad limit"
    # S22IT1S0P2-41: element nowego planu po zmianie terminu zadania, czekający na potwierdzenie delegowanego
    NOWY_PLAN = "nowy plan"


class StatusZmianyTerminu:
    """Stan aktualizacji planu delegacji po zmianie terminu zadania (S22IT1S0P2-41)."""

    OCZEKUJE = "oczekuje na potwierdzenie"
    POTWIERDZONA = "potwierdzona"
    WSTRZYMANA = "wstrzymana"  # delegowany nie może wziąć udziału – sprawa u przełożonego (3a–4a)
    DO_UZGODNIENIA = "do uzgodnienia"  # nie da się przygotować planu w limitach – sprawa u przełożonego
    ZASTAPIONA = "zastąpiona"  # kolejna zmiana terminu przed potwierdzeniem


class StatusZlecenia:
    """Stan zlecenia agentowi, którego realizacja przekroczyła dopuszczalny czas (S22IT1S0P2-42)."""

    OPOZNIONE = "opóźnione"  # przekroczony czas, zlecenie ponowione – czeka na decyzję delegowanego
    OCZEKIWANIE = "oczekiwanie potwierdzone"  # delegowany czeka na realizację
    ZREALIZOWANE = "zrealizowane"  # agent odpowiedział po przywróceniu działania
    WYCOFANE = "wycofane"  # rezygnacja albo wyczerpane ponowienia – incydent, sprawa u osoby odpowiedzialnej


class StatusZmianyUslugi:
    """Stan zmiany usługi w trakcie podróży zgłoszonej przez dostawcę (S22IT1S0P2-34)."""

    OCZEKUJE = "oczekuje na odpowiedź"  # rozwiązanie zastępcze czeka na akceptację delegowanego
    ZAAKCEPTOWANA = "zaakceptowana"  # plan i dokumentacja zaktualizowane, przełożony poinformowany
    U_DYZURNEGO = "u osoby dyżurnej"  # brak rozwiązania, odrzucenie albo brak odpowiedzi w terminie
    BEZ_ZMIAN = "bez zmian"  # zmiana nie wymaga rozwiązania zastępczego – tylko informacja


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
    # Osoba dyżurna jednostki – przejmuje zmiany w podróży bez odpowiedzi delegowanego (S22IT1S0P2-34)
    id_dyzurnego: Mapped[str | None] = mapped_column(String(20))


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


class PrzynaleznoscJednostki(Base):
    __tablename__ = "przynaleznosc_jednostki"

    id_przynaleznosci: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    id_zolnierza: Mapped[str] = mapped_column(ForeignKey("zolnierz.id_zolnierza"), index=True)
    id_jednostki: Mapped[str] = mapped_column(ForeignKey("jednostka.id_jednostki"), index=True)
    data_od: Mapped[date] = mapped_column(Date)
    data_do: Mapped[date | None] = mapped_column(Date)
    powod: Mapped[str | None] = mapped_column(Text)
    data_utworzenia: Mapped[datetime] = mapped_column(DateTime, default=teraz)
    id_operatora: Mapped[str | None] = mapped_column(String(20))
    zrodlo: Mapped[str | None] = mapped_column(String(30))


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
    id_jednostki: Mapped[str | None] = mapped_column(ForeignKey("jednostka.id_jednostki"), index=True)
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


class ZmianaTerminu(Base):
    """Zmiana terminu zadania i aktualizacja planu delegacji (S22IT1S0P2-41) – rejestr zmian z porównaniem planów."""

    __tablename__ = "zmiana_terminu"

    id_zmiany: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    id_delegacji: Mapped[str] = mapped_column(ForeignKey("delegacja.id_delegacji"), index=True)
    id_przedsiewziecia: Mapped[str | None] = mapped_column(ForeignKey("przedsiewziecie.id_przedsiewziecia"))
    data_zgloszenia: Mapped[datetime] = mapped_column(DateTime, default=teraz)
    powod: Mapped[str | None] = mapped_column(Text)
    # Termin zadania, do którego pasują dotychczasowe daty delegacji, i nowy termin zadania
    poczatek_zadania_przed: Mapped[date] = mapped_column(Date)
    koniec_zadania_przed: Mapped[date] = mapped_column(Date)
    poczatek_zadania: Mapped[date] = mapped_column(Date)
    koniec_zadania: Mapped[date] = mapped_column(Date)
    stary_wyjazd: Mapped[date | None] = mapped_column(Date)
    stary_powrot: Mapped[date | None] = mapped_column(Date)
    nowy_wyjazd: Mapped[date] = mapped_column(Date)
    nowy_powrot: Mapped[date] = mapped_column(Date)
    # Plany w postaci do porównania (termin, przejazdy, nocleg, koszt) – stan z chwili przygotowania zmiany
    stary_plan: Mapped[dict] = mapped_column(JSON, default=dict)
    nowy_plan: Mapped[dict | None] = mapped_column(JSON)
    roznica_kosztu: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    status: Mapped[str] = mapped_column(String(30))
    data_decyzji: Mapped[datetime | None] = mapped_column(DateTime)
    uzasadnienie: Mapped[str | None] = mapped_column(Text)


class ZlecenieAgenta(Base):
    """Zlecenie agentowi, które przekroczyło dopuszczalny czas realizacji (S22IT1S0P2-42): ponowienia i incydent."""

    __tablename__ = "zlecenie_agenta"

    id_zlecenia: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    id_delegacji: Mapped[str] = mapped_column(ForeignKey("delegacja.id_delegacji"), index=True)
    rodzaj: Mapped[str] = mapped_column(String(30))  # np. „przygotowanie transportu”
    data_zlecenia: Mapped[datetime] = mapped_column(DateTime)
    liczba_ponowien: Mapped[int] = mapped_column(Integer, default=0)
    przewidywany_termin: Mapped[datetime] = mapped_column(DateTime)
    status: Mapped[str] = mapped_column(String(30))
    data_zakonczenia: Mapped[datetime | None] = mapped_column(DateTime)
    incydent: Mapped[str | None] = mapped_column(Text)


class ZmianaUslugi(Base):
    """Zmiana usługi w trakcie podróży zgłoszona przez dostawcę i jej obsługa – historia delegacji (S22IT1S0P2-34)."""

    __tablename__ = "zmiana_uslugi"

    id_zmiany: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    id_delegacji: Mapped[str] = mapped_column(ForeignKey("delegacja.id_delegacji"), index=True)
    usluga: Mapped[str] = mapped_column(String(20))  # transport / zakwaterowanie
    rodzaj_zdarzenia: Mapped[str] = mapped_column(String(20))  # odwołanie / opóźnienie / zmiana terminu / ...
    id_przejazdu: Mapped[int | None] = mapped_column(ForeignKey("przejazd.id_przejazdu"))
    id_zakwaterowania: Mapped[int | None] = mapped_column(ForeignKey("zakwaterowanie.id_zakwaterowania"))
    data_zdarzenia: Mapped[date] = mapped_column(Date)
    opis: Mapped[str | None] = mapped_column(Text)
    data_zgloszenia: Mapped[datetime] = mapped_column(DateTime)
    id_przejazdu_zastepczego: Mapped[int | None] = mapped_column(ForeignKey("przejazd.id_przejazdu"))
    id_zakwaterowania_zastepczego: Mapped[int | None] = mapped_column(ForeignKey("zakwaterowanie.id_zakwaterowania"))
    koszt_zastepczy: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    zgodny_z_limitem: Mapped[bool | None] = mapped_column(Boolean)
    termin_odpowiedzi: Mapped[datetime | None] = mapped_column(DateTime)
    status: Mapped[str] = mapped_column(String(30))
    data_odpowiedzi: Mapped[datetime | None] = mapped_column(DateTime)
    id_dyzurnego: Mapped[str | None] = mapped_column(String(20))


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
