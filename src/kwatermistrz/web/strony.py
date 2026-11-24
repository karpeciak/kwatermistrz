"""Interfejs użytkownika (`/app`) – strony generowane przez FastAPI (Jinja2) z HTMX do odświeżania fragmentów.

Logowanie jest demonstracyjne: użytkownik wybiera żołnierza z listy, a jego identyfikator trafia do ciasteczka.
Docelowe uwierzytelnianie (np. konto uczelniane / Entra ID) – osobna historyjka.

Strony do rozbudowy w historyjkach sprintu 1:
- S22IT1S0P2-37 wniosek roboczy, -33 podsumowanie przed złożeniem → `nowy_wniosek.html`,
- S22IT1S0P2-32 pełna historia i czas oczekiwania → `wniosek.html`,
- S22IT1S0P2-35 zbiorcze zatwierdzanie z budżetem → `do_zatwierdzenia.html`,
- S22IT1S0P2-38, -27, -39 polecenie wyjazdu, wybór przejazdu i noclegu → nowe sekcje w `wniosek.html`,
- S22IT1S0P2-36, -20, -43 rozliczenie → `rozliczenie.html` (-43: przekazanie do weryfikacji i akceptacja).
"""

from datetime import datetime, timedelta
from pathlib import Path
from typing import Annotated
from uuid import uuid4
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..agents.koordynator import brakujace_elementy_organizacji
from ..api.organizacja import przyjmij_wariant, przyjmij_wybor
from ..api.rozliczenia import (
    KOMUNIKAT_PRZYJETY,
    KOMUNIKAT_ZATWIERDZONE,
    DokumentKosztowyIn,
    korekty_rozliczenia,
    mozna_edytowac,
    przekaz_rozliczenie,
    przyjmij_dokument,
    zapisz_podroz,
    zatwierdz_wyliczenie,
    zlec_weryfikacje,
    zlec_wyliczenie,
)
from ..api.schemas import WniosekIn
from ..api.wnioski import (
    etap_przygotowania,
    usun_wersje_robocza,
    wersja_robocza,
    zapisz_wersje_robocza,
    zarejestruj_wniosek,
)
from ..api.zaleznosci import chron_formularze, dostep_do_podrozy, get_broker, get_sesja, sprawdz_csrf
from ..api.zatwierdzenia import (
    budzet_jednostki,
    decyzja_zbiorcza,
    koszt_delegacji,
    oczekujace_na_decyzje,
    wstrzymane_z_powodu_budzetu,
)
from ..config import settings
from ..db import teraz
from ..messaging import Broker
from ..miejscowosci import BladMiejscowosci, ZrodloNiedostepne, szukaj_miejscowosci
from ..models import (
    Agent,
    Delegacja,
    DokumentKosztowy,
    Jednostka,
    LogDecyzji,
    LogOperacji,
    Przedsiewziecie,
    Przejazd,
    Rozliczenie,
    Status,
    StatusRezerwacji,
    Zolnierz,
)
from ..podroze import (
    KRYTERIA,
    SRODKI_TRANSPORTU,
    Kryterium,
    WariantPodrozyIn,
    WyborPodrozy,
    decyzja_do_uzgodnienia,
    zestawienie,
)
from ..sesje import CIASTECZKO, CZAS_SESJI, odczytaj_sesje, token_csrf, utworz_sesje
from ..walidacja import parsuj_date, waliduj_wniosek

KATALOG = Path(__file__).parent
STREFA = ZoneInfo("Europe/Warsaw")

# Sam status nie potwierdza pracy agenta. Krótkie odpytywanie służy oczekiwaniu na odpowiedź.
ETAPY_W_TOKU = {Status.ZLOZONY, Status.ZATWIERDZONY}
ETAPY_PODROZY = (Status.ZATWIERDZONY, Status.ZORGANIZOWANY, Status.W_TRAKCIE, Status.DO_ROZLICZENIA, Status.ROZLICZONY)

# S22IT1S0P2-32: ścieżka etapów obsługi wniosku i statusy boczne
ETAPY_PROCESU = [
    Status.ZLOZONY,
    Status.W_AKCEPTACJI,
    Status.ZATWIERDZONY,
    Status.ZORGANIZOWANY,
    Status.W_TRAKCIE,
    Status.DO_ROZLICZENIA,
    Status.ROZLICZONY,
]
# Status boczny → etap ścieżki, na którym zatrzymał się wniosek
ETAP_STATUSU_BOCZNEGO = {Status.DO_UZUPELNIENIA: Status.ZLOZONY, Status.ODRZUCONY: Status.W_AKCEPTACJI}
# Nazwy operacji Agenta Zatwierdzeń (agents/zatwierdzenia.py) – daty eskalacji bierzemy z logu operacji
OPERACJA_PRZYPOMNIENIA = "Przypomnienie o oczekującym wniosku"
OPERACJA_PRZEKAZANIA = "Przekazanie wniosku zastępcy"
# Po tylu minutach bez aktywności agentów status „w toku” może być nieaktualny (S22IT1S0P2-32, scenariusz 3a)
NIEAKTUALNY_PO = timedelta(minutes=10)
# Słownik statusów delegacji (S22IT1S0P2-32) – co oznacza każdy etap obsługi, w języku wnioskodawcy
SLOWNIK_STATUSOW = {
    Status.ROBOCZY: "Wersja robocza – wniosek nie został jeszcze złożony.",
    Status.ZLOZONY: "Wniosek wpłynął do systemu – agenci sprawdzają jego kompletność i poprawność.",
    Status.DO_UZUPELNIENIA: "Wniosek wymaga poprawek – uzupełnij wskazane dane i złóż go ponownie.",
    Status.W_AKCEPTACJI: "Wniosek czeka na decyzję przełożonego.",
    Status.ZATWIERDZONY: "Przełożony zgodził się na wyjazd. Sprawdź przygotowanie podróży.",
    Status.ODRZUCONY: "Przełożony nie wyraził zgody na wyjazd.",
    Status.ZORGANIZOWANY: "Wyjazd jest przygotowany – polecenie wyjazdu, przejazd i nocleg są gotowe.",
    Status.W_TRAKCIE: "Trwa wyjazd służbowy.",
    Status.DO_ROZLICZENIA: "Wyjazd się zakończył – złóż rozliczenie z dokumentami kosztowymi.",
    Status.ROZLICZONY: "Rozliczenie zostało sprawdzone i przekazane do komórki finansowej.",
}
WYNIK_DOKUMENT_PRZYJETY = "dokument-przyjety"

szablony = Jinja2Templates(directory=KATALOG / "szablony")
router = APIRouter(prefix="/app", include_in_schema=False, dependencies=[Depends(chron_formularze)])


# ---------------------------------------------------------------- filtry szablonów


def czas_lokalny(wartosc: datetime | None) -> str:
    if wartosc is None:
        return "–"
    return wartosc.replace(tzinfo=ZoneInfo("UTC")).astimezone(STREFA).strftime("%d.%m.%Y %H:%M")


def ile_czasu(od: datetime | None) -> str:
    if od is None:
        return "–"
    minuty = int((teraz() - od).total_seconds() // 60)
    if minuty < 60:
        return f"{minuty} min"
    if minuty < 24 * 60:
        return f"{minuty // 60} h {minuty % 60} min"
    return f"{minuty // (24 * 60)} dni {minuty % (24 * 60) // 60} h"


def czas_na_etapie(od: datetime | None) -> str:
    """Czas oczekiwania na etapie w dniach (S22IT1S0P2-32); poniżej doby – w godzinach i minutach."""
    if od is None:
        return "–"
    dni = (teraz() - od).days
    if dni < 1:
        return ile_czasu(od)
    return "1 dzień" if dni == 1 else f"{dni} dni"


def osoba(z: Zolnierz | None) -> str:
    """Osoba odpowiedzialna: stopień, imię i nazwisko, stanowisko (S22IT1S0P2-32)."""
    if z is None:
        return "–"
    tekst = f"{z.stopien} {z.imie_nazwisko}"
    return f"{tekst}, {z.funkcja}" if z.funkcja else tekst


def data_pl(wartosc: str | None) -> str:
    """Data z wniosku (RRRR-MM-DD lub DD.MM.RRRR) w formacie DD.MM.RRRR."""
    if not wartosc:
        return "–"
    rok, _, reszta = str(wartosc).partition("-")
    if reszta and len(rok) == 4:
        miesiac, _, dzien = reszta.partition("-")
        return f"{dzien}.{miesiac}.{rok}"
    return str(wartosc)


def klasa_statusu(status: str) -> str:
    return "status-" + status.replace(" ", "-").replace("ł", "l").replace("ż", "z").replace("ę", "e")


szablony.env.filters.update(
    czas=czas_lokalny,
    ile_czasu=ile_czasu,
    klasa_statusu=klasa_statusu,
    data_pl=data_pl,
    czas_na_etapie=czas_na_etapie,
    osoba=osoba,
)
szablony.env.globals["slownik_statusow"] = SLOWNIK_STATUSOW


# ---------------------------------------------------------------- użytkownik


def biezacy_uzytkownik(request: Request, s: Session = Depends(get_sesja)) -> Zolnierz:
    id_zolnierza = odczytaj_sesje(request.cookies.get(CIASTECZKO), settings.session_secret.get_secret_value())
    uzytkownik = s.get(Zolnierz, id_zolnierza) if id_zolnierza else None
    if uzytkownik is None:
        if request.headers.get("HX-Request", "").lower() == "true":
            raise HTTPException(401, "Sesja wygasła. Zaloguj się ponownie.", headers={"HX-Redirect": "/app/logowanie"})
        raise HTTPException(303, headers={"Location": "/app/logowanie"})
    return uzytkownik


@router.get("/miejscowosci", response_class=HTMLResponse)
def wyszukaj_miejscowosci(
    request: Request,
    miejsce_docelowe: str = "",
    uzytkownik: Zolnierz = Depends(biezacy_uzytkownik),
):
    wyniki, blad = [], ""
    try:
        wyniki = szukaj_miejscowosci(miejsce_docelowe)
    except (BladMiejscowosci, ZrodloNiedostepne) as e:
        blad = str(e)
    return szablony.TemplateResponse(
        request=request, name="miejscowosci.html", context={"wyniki": wyniki, "blad": blad}
    )


def liczba_do_zatwierdzenia(s: Session, uzytkownik: Zolnierz) -> int:
    return s.scalar(
        select(func.count()).where(
            Delegacja.status == Status.W_AKCEPTACJI, Delegacja.id_zatwierdzajacego == uzytkownik.id_zolnierza
        )
    )


def strona(request: Request, s: Session, szablon: str, uzytkownik: Zolnierz | None = None, **kontekst):
    kontekst.setdefault("srodki_transportu", SRODKI_TRANSPORTU)
    if uzytkownik is not None:
        kontekst["do_zatwierdzenia"] = liczba_do_zatwierdzenia(s, uzytkownik)
        kontekst["csrf"] = token_csrf(request.cookies.get(CIASTECZKO, ""), settings.session_secret.get_secret_value())
    return szablony.TemplateResponse(request, szablon, {"uzytkownik": uzytkownik, **kontekst})


# ---------------------------------------------------------------- logowanie


@router.get("", response_class=HTMLResponse)
def start():
    return RedirectResponse("/app/pulpit", status_code=303)


@router.get("/logowanie", response_class=HTMLResponse)
def logowanie(request: Request, s: Session = Depends(get_sesja)):
    zolnierze = s.scalars(select(Zolnierz).order_by(Zolnierz.id_zolnierza)).all()
    return strona(request, s, "logowanie.html", zolnierze=zolnierze)


@router.post("/logowanie")
def zaloguj(request: Request, id_zolnierza: str = Form(...), s: Session = Depends(get_sesja)):
    if s.get(Zolnierz, id_zolnierza) is None:
        raise HTTPException(404, "Nie ma takiego żołnierza")
    odpowiedz = RedirectResponse("/app/pulpit", status_code=303)
    odpowiedz.set_cookie(
        CIASTECZKO,
        utworz_sesje(id_zolnierza, settings.session_secret.get_secret_value()),
        max_age=CZAS_SESJI,
        httponly=True,
        samesite="lax",
        secure=request.url.scheme == "https",
    )
    return odpowiedz


@router.post("/wyloguj")
def wyloguj(request: Request, csrf: str = Form("")):
    sprawdz_csrf(request, csrf)
    odpowiedz = RedirectResponse("/app/logowanie", status_code=303)
    odpowiedz.delete_cookie(CIASTECZKO)
    return odpowiedz


# ---------------------------------------------------------------- transport S22IT1S0P2-27, -57


@router.get("/podroze", response_class=HTMLResponse)
def lista_podrozy(
    request: Request,
    s: Session = Depends(get_sesja),
    uzytkownik: Zolnierz = Depends(biezacy_uzytkownik),
):
    wnioski = s.scalars(
        select(Delegacja)
        .where(
            Delegacja.status.in_(ETAPY_PODROZY),
            (Delegacja.id_zolnierza == uzytkownik.id_zolnierza)
            | (Delegacja.id_zatwierdzajacego == uzytkownik.id_zolnierza),
        )
        .order_by(Delegacja.data_zmiany_statusu.desc(), Delegacja.id_delegacji)
    ).all()
    return strona(
        request,
        s,
        "podroze.html",
        uzytkownik,
        wnioski=wnioski,
        wnioskodawcy={z.id_zolnierza: z for z in s.scalars(select(Zolnierz))},
    )


def widok_podrozy(
    id_delegacji: str,
    request: Request,
    s: Session,
    uzytkownik: Zolnierz,
    kryterium: Kryterium = "standardowe",
    przekazano: bool = False,
    oferta: str | None = None,
    blad: str | None = None,
    status: int = 200,
    dane_oferty: dict | None = None,
    dane_wyboru: dict | None = None,
):
    d = dostep_do_podrozy(s, id_delegacji, uzytkownik)
    dane = zestawienie(s, d, kryterium)
    rozstrzygniete = dane["do_uzgodnienia"] or any(w["status"] != "propozycja" for w in dane["warianty"])
    odpowiedz = strona(
        request,
        s,
        "podroz.html",
        uzytkownik,
        d=d,
        **dane,
        kryteria=KRYTERIA,
        przekazano=przekazano,
        rozstrzygniete=rozstrzygniete,
        moze_wybrac=d.status == Status.ZATWIERDZONY and uzytkownik.id_zolnierza == d.id_zatwierdzajacego,
        blad=blad,
        dane_oferty=dane_oferty or {"klucz": str(uuid4())},
        dane_wyboru=dane_wyboru or {},
        oferta_zapisana=oferta and any(w["id_polaczenia"] == oferta for w in dane["warianty"]),
        srodki_transportu=SRODKI_TRANSPORTU,
    )
    odpowiedz.status_code = status
    return odpowiedz


@router.get("/wnioski/{id_delegacji}/podroz", response_class=HTMLResponse)
def podroz(
    id_delegacji: str,
    request: Request,
    kryterium: Kryterium = "standardowe",
    przekazano: bool = False,
    oferta: str | None = None,
    s: Session = Depends(get_sesja),
    uzytkownik: Zolnierz = Depends(biezacy_uzytkownik),
):
    return widok_podrozy(id_delegacji, request, s, uzytkownik, kryterium, przekazano, oferta)


@router.post("/wnioski/{id_delegacji}/podroz/warianty", response_class=HTMLResponse)
async def uzupelnij_wariant(
    id_delegacji: str,
    request: Request,
    s: Session = Depends(get_sesja),
    broker: Broker = Depends(get_broker),
    uzytkownik: Zolnierz = Depends(biezacy_uzytkownik),
):
    formularz = await request.form()
    sprawdz_csrf(request, formularz.get("csrf"))
    dostep_do_podrozy(s, id_delegacji, uzytkownik, zapis=True)
    dane = {pole: str(formularz.get(pole, "")) for pole in WariantPodrozyIn.model_fields}
    try:
        wariant = WariantPodrozyIn.model_validate(dane)
        await przyjmij_wariant(s, broker, id_delegacji, uzytkownik, wariant)
    except ValidationError as e:
        b = e.errors()[0]
        etykiety = {
            "klucz": "identyfikator formularza (odśwież stronę)",
            "kierunek": "kierunek",
            "przewoznik": "przewoźnik",
            "srodek_transportu": "środek transportu",
            "godzina_odjazdu": "godzina odjazdu",
            "czas_min": "czas przejazdu (1–2880 minut)",
            "koszt": "koszt (nieujemna kwota, do dwóch miejsc po przecinku)",
            "liczba_przesiadek": "liczba przesiadek (0–20)",
            "punktualnosc": "punktualność (0–100%)",
            "ocena_bezpieczenstwa": "ocena bezpieczeństwa (1–5)",
            "zrodlo": "źródło i podstawa oceny",
        }
        return widok_podrozy(
            id_delegacji,
            request,
            s,
            uzytkownik,
            status=422,
            dane_oferty=dane,
            blad="Sprawdź pole: " + etykiety.get(str(b["loc"][0]), "dane oferty") + ".",
        )
    except HTTPException as e:
        if e.status_code not in (400, 409, 422):
            raise
        return widok_podrozy(
            id_delegacji, request, s, uzytkownik, status=e.status_code, dane_oferty=dane, blad=str(e.detail)
        )
    return RedirectResponse(f"/app/wnioski/{id_delegacji}/podroz?oferta={wariant.id_polaczenia}", status_code=303)


@router.post("/wnioski/{id_delegacji}/podroz")
async def wybierz_podroz(
    id_delegacji: str,
    request: Request,
    id_tam: str = Form(""),
    id_powrot: str = Form(""),
    akcja: str = Form("wybierz"),
    kryterium: Annotated[Kryterium, Form()] = "standardowe",
    zgoda_na_zmiane_transportu: Annotated[bool, Form()] = False,
    uzasadnienie: str = Form(""),
    csrf: str = Form(""),
    s: Session = Depends(get_sesja),
    broker: Broker = Depends(get_broker),
    uzytkownik: Zolnierz = Depends(biezacy_uzytkownik),
):
    sprawdz_csrf(request, csrf)
    dostep_do_podrozy(s, id_delegacji, uzytkownik, zapis=True)
    if akcja not in ("wybierz", "odrzuc"):
        raise HTTPException(400, "Nieznana decyzja")
    try:
        dane = WyborPodrozy(
            id_tam=(id_tam or None) if akcja == "wybierz" else None,
            id_powrot=(id_powrot or None) if akcja == "wybierz" else None,
            odrzuc_wszystkie=akcja == "odrzuc",
            kryterium=kryterium,
            zgoda_na_zmiane_transportu=zgoda_na_zmiane_transportu,
            uzasadnienie=uzasadnienie,
        )
        await przyjmij_wybor(s, broker, id_delegacji, uzytkownik, dane)
    except (ValidationError, HTTPException) as e:
        if isinstance(e, HTTPException) and e.status_code not in (400, 409, 422):
            raise
        return widok_podrozy(
            id_delegacji,
            request,
            s,
            uzytkownik,
            kryterium=kryterium,
            status=e.status_code if isinstance(e, HTTPException) else 422,
            blad=str(e.detail)
            if isinstance(e, HTTPException)
            else "Wybierz oba przejazdy. Przy odrzuceniu wszystkich wariantów podaj uzasadnienie.",
            dane_wyboru={
                "id_tam": id_tam,
                "id_powrot": id_powrot,
                "uzasadnienie": uzasadnienie,
                "zgoda_na_zmiane_transportu": zgoda_na_zmiane_transportu,
            },
        )
    return RedirectResponse(f"/app/wnioski/{id_delegacji}/podroz?przekazano=true", status_code=303)


# ---------------------------------------------------------------- wnioskodawca


@router.get("/pulpit", response_class=HTMLResponse)
def pulpit(request: Request, s: Session = Depends(get_sesja), uzytkownik: Zolnierz = Depends(biezacy_uzytkownik)):
    wnioski = s.scalars(
        select(Delegacja)
        .where(Delegacja.id_zolnierza == uzytkownik.id_zolnierza, Delegacja.status != Status.ROBOCZY)
        .order_by(Delegacja.data_zmiany_statusu.desc())
    ).all()
    id_osob = {d.id_zatwierdzajacego for d in wnioski if d.id_zatwierdzajacego}
    osoby = {z.id_zolnierza: z for z in s.scalars(select(Zolnierz).where(Zolnierz.id_zolnierza.in_(id_osob)))}
    robocza = wersja_robocza(s, uzytkownik.id_zolnierza)
    return strona(request, s, "pulpit.html", uzytkownik, wnioski=wnioski, osoby=osoby, robocza=robocza)


@router.get("/wnioski/nowy", response_class=HTMLResponse)
def nowy_wniosek(request: Request, s: Session = Depends(get_sesja), uzytkownik: Zolnierz = Depends(biezacy_uzytkownik)):
    przedsiewziecia = s.scalars(select(Przedsiewziecie).order_by(Przedsiewziecie.data_rozpoczecia)).all()
    jednostka = s.get(Jednostka, uzytkownik.id_jednostki)
    robocza = wersja_robocza(s, uzytkownik.id_zolnierza)  # S22IT1S0P2-80: wznowienie
    kontekst = dict(przedsiewziecia=przedsiewziecia, jednostka=jednostka, robocza=robocza)
    w = robocza.dane_wniosku if robocza else None
    return strona(request, s, "nowy_wniosek.html", uzytkownik, **kontekst, w=w)


@router.post("/wnioski/nowy")
async def zloz_nowy_wniosek(
    id_przedsiewziecia: str = Form(""),
    cel_wyjazdu: str = Form(""),
    miejsce_docelowe: str = Form(""),
    data_wyjazdu: str = Form(""),
    data_powrotu: str = Form(""),
    podstawa_numer_rozkazu: str = Form(""),
    podstawa_data_rozkazu: str = Form(""),
    srodek_transportu: str = Form("kolej"),
    potrzeba_zakwaterowania: bool = Form(False),
    nr_rejestracyjny: str = Form(""),
    pojemnosc_silnika: str = Form(""),
    s: Session = Depends(get_sesja),
    broker: Broker = Depends(get_broker),
    uzytkownik: Zolnierz = Depends(biezacy_uzytkownik),
):
    przedsiewziecie = s.get(Przedsiewziecie, id_przedsiewziecia) if id_przedsiewziecia else None
    if przedsiewziecie:
        # puste pola uzupełniamy danymi wybranego przedsięwzięcia
        cel_wyjazdu = cel_wyjazdu or przedsiewziecie.nazwa
        miejsce_docelowe = miejsce_docelowe or przedsiewziecie.miejscowosc
        data_wyjazdu = data_wyjazdu or przedsiewziecie.data_rozpoczecia.isoformat()
        data_powrotu = data_powrotu or przedsiewziecie.data_zakonczenia.isoformat()
    wniosek = WniosekIn(
        id_zolnierza=uzytkownik.id_zolnierza,
        id_przedsiewziecia=przedsiewziecie.id_przedsiewziecia if przedsiewziecie else None,
        cel_wyjazdu=cel_wyjazdu or None,
        miejsce_docelowe=miejsce_docelowe or None,
        data_wyjazdu=data_wyjazdu or None,
        data_powrotu=data_powrotu or None,
        srodek_transportu=srodek_transportu or None,
        potrzeba_zakwaterowania=potrzeba_zakwaterowania,
        dane_transportu=dane_pojazdu(srodek_transportu, nr_rejestracyjny, pojemnosc_silnika),
        podstawa_numer_rozkazu=podstawa_numer_rozkazu or None,
        podstawa_data_rozkazu=podstawa_data_rozkazu or None,
    )
    d = await zarejestruj_wniosek(s, broker, wniosek)
    return RedirectResponse(f"/app/wnioski/{d.id_delegacji}", status_code=303)


def dane_pojazdu(srodek_transportu: str, nr_rejestracyjny: str, pojemnosc_silnika: str) -> dict[str, str]:
    """Dane pojazdu prywatnego (S22IT1S0P2-12) – tylko gdy wybrano ten środek transportu."""
    if srodek_transportu != "samochód prywatny":
        return {}
    dane = {"nr_rejestracyjny": nr_rejestracyjny.strip(), "pojemnosc_silnika": pojemnosc_silnika.strip()}
    return {pole: wartosc for pole, wartosc in dane.items() if wartosc}


@router.post("/wnioski/nowy/podsumowanie", response_class=HTMLResponse)
def podsumowanie_wniosku(
    request: Request,
    popraw: bool = False,
    id_przedsiewziecia: str = Form(""),
    cel_wyjazdu: str = Form(""),
    miejsce_docelowe: str = Form(""),
    data_wyjazdu: str = Form(""),
    data_powrotu: str = Form(""),
    podstawa_numer_rozkazu: str = Form(""),
    podstawa_data_rozkazu: str = Form(""),
    srodek_transportu: str = Form("kolej"),
    potrzeba_zakwaterowania: bool = Form(False),
    nr_rejestracyjny: str = Form(""),
    pojemnosc_silnika: str = Form(""),
    s: Session = Depends(get_sesja),
    uzytkownik: Zolnierz = Depends(biezacy_uzytkownik),
):
    """S22IT1S0P2-52: sprawdzenie kompletności i podsumowanie przed złożeniem – wniosek nie jest jeszcze zapisywany."""
    p = s.get(Przedsiewziecie, id_przedsiewziecia) if id_przedsiewziecia else None
    w = {
        "id_przedsiewziecia": p.id_przedsiewziecia if p else "",
        "cel_wyjazdu": cel_wyjazdu.strip() or (p.nazwa if p else ""),
        "miejsce_docelowe": miejsce_docelowe.strip() or (p.miejscowosc if p else ""),
        "data_wyjazdu": data_wyjazdu or (p.data_rozpoczecia.isoformat() if p else ""),
        "data_powrotu": data_powrotu or (p.data_zakonczenia.isoformat() if p else ""),
        "podstawa_numer_rozkazu": podstawa_numer_rozkazu.strip(),
        "podstawa_data_rozkazu": podstawa_data_rozkazu,
        "srodek_transportu": srodek_transportu,
        "potrzeba_zakwaterowania": potrzeba_zakwaterowania,
        "dane_transportu": dane_pojazdu(srodek_transportu, nr_rejestracyjny, pojemnosc_silnika),
    }
    jednostka = s.get(Jednostka, uzytkownik.id_jednostki)
    bledy = [] if popraw else waliduj_wniosek(w, jednostka=uzytkownik.id_jednostki, wymagaj_podstawy=True)
    etap = etap_przygotowania(w) if popraw or bledy else "podsumowanie"
    zapisz_wersje_robocza(s, uzytkownik.id_zolnierza, w, etap)  # S22IT1S0P2-37
    if popraw or bledy:
        # scenariusz 3a–4a: wskazanie brakujących lub błędnych danych, formularz z wpisanymi już wartościami
        przedsiewziecia = s.scalars(select(Przedsiewziecie).order_by(Przedsiewziecie.data_rozpoczecia)).all()
        bledy_pol = {b.pole: b.komunikat for b in bledy}
        kontekst = dict(przedsiewziecia=przedsiewziecia, jednostka=jednostka, w=w, bledy=bledy, bledy_pol=bledy_pol)
        return strona(request, s, "nowy_wniosek.html", uzytkownik, **kontekst)
    liczba_dni = (parsuj_date(w["data_powrotu"]) - parsuj_date(w["data_wyjazdu"])).days + 1
    kontekst = dict(w=w, jednostka=jednostka, przedsiewziecie=p, liczba_dni=liczba_dni)
    return strona(request, s, "podsumowanie_wniosku.html", uzytkownik, **kontekst)


# ---------------------------------------------------------------- wersja robocza wniosku (S22IT1S0P2-37)

POLA_WNIOSKU = (
    "id_przedsiewziecia",
    "cel_wyjazdu",
    "miejsce_docelowe",
    "data_wyjazdu",
    "data_powrotu",
    "podstawa_numer_rozkazu",
    "podstawa_data_rozkazu",
    "srodek_transportu",
)


def dane_z_formularza(formularz) -> dict:
    """Dane wniosku z formularza w postaci zapisywanej w wersji roboczej."""
    w = {pole: str(formularz.get(pole) or "").strip() for pole in POLA_WNIOSKU}
    w["potrzeba_zakwaterowania"] = formularz.get("potrzeba_zakwaterowania") == "true"
    w["dane_transportu"] = dane_pojazdu(
        w["srodek_transportu"],
        str(formularz.get("nr_rejestracyjny") or ""),
        str(formularz.get("pojemnosc_silnika") or ""),
    )
    return w


@router.post("/wnioski/nowy/roboczy", response_class=HTMLResponse)
async def zapisz_wersje_robocza_w_interfejsie(
    request: Request,
    wyjdz: bool = False,
    s: Session = Depends(get_sesja),
    uzytkownik: Zolnierz = Depends(biezacy_uzytkownik),
):
    """S22IT1S0P2-79: autozapis podczas wypełniania (HTMX) albo „Zapisz i dokończ później”."""
    w = dane_z_formularza(await request.form())
    d = zapisz_wersje_robocza(s, uzytkownik.id_zolnierza, w, etap_przygotowania(w))
    if wyjdz:
        return RedirectResponse("/app/pulpit?wynik=wersja-robocza", status_code=303)
    return HTMLResponse(f"<small>Zapisano wersję roboczą · {czas_lokalny(d.data_ostatniej_zmiany)}</small>")


@router.post("/wnioski/nowy/roboczy/rezygnacja", response_class=HTMLResponse)
def rezygnacja_z_wersji_roboczej(
    request: Request,
    s: Session = Depends(get_sesja),
    uzytkownik: Zolnierz = Depends(biezacy_uzytkownik),
):
    """S22IT1S0P2-81, scenariusz 5a–6a: usunięcie zachowanych danych i nowy, pusty wniosek."""
    usun_wersje_robocza(s, uzytkownik.id_zolnierza)
    s.commit()
    kontekst = dict(
        przedsiewziecia=s.scalars(select(Przedsiewziecie).order_by(Przedsiewziecie.data_rozpoczecia)).all(),
        jednostka=s.get(Jednostka, uzytkownik.id_jednostki),
        komunikat="Usunięto wersję roboczą – możesz zacząć nowy wniosek.",
    )
    return strona(request, s, "nowy_wniosek.html", uzytkownik, **kontekst)


def dane_wniosku(s: Session, id_delegacji: str, uzytkownik: Zolnierz) -> dict:
    d = s.get(Delegacja, id_delegacji)
    if d is None or uzytkownik.id_zolnierza not in (d.id_zolnierza, d.id_zatwierdzajacego):
        raise HTTPException(404, "Nie znaleziono wniosku")
    operacje = s.scalars(
        select(LogOperacji).where(LogOperacji.id_delegacji == id_delegacji).order_by(LogOperacji.id_operacji)
    ).all()
    decyzje: dict[int, list[LogDecyzji]] = {}
    for dec in s.scalars(select(LogDecyzji).where(LogDecyzji.id_operacji.in_([o.id_operacji for o in operacje]))):
        decyzje.setdefault(dec.id_operacji, []).append(dec)
    bledy = []
    if d.status == Status.DO_UZUPELNIENIA:
        walidacje = [
            x for o in operacje for x in decyzje.get(o.id_operacji, []) if x.typ_decyzji == "Walidacja wniosku"
        ]
        if walidacje and walidacje[-1].wybrany_wariant == "Do poprawy":
            bledy = walidacje[-1].uzasadnienie.split("; ")
    oczekiwanie = None
    wynik_transportu = any(o.id_agenta == "AG-04" for o in operacje)
    odswiezaj = d.status in ETAPY_W_TOKU and (teraz() - d.data_zmiany_statusu).total_seconds() < 60
    if d.status == Status.ZLOZONY:
        oczekiwanie = "Oczekiwanie na obsługę wniosku."
    elif d.status == Status.W_AKCEPTACJI:
        oczekiwanie = "Wniosek oczekuje na decyzję przełożonego."
    elif d.status == Status.ZATWIERDZONY:
        warianty = list(s.scalars(select(Przejazd).where(Przejazd.id_delegacji == id_delegacji)))
        if decyzja_do_uzgodnienia(s, d) or any(r.status == StatusRezerwacji.DO_UZGODNIENIA for r in warianty):
            oczekiwanie = "Podróż została przekazana do dodatkowego uzgodnienia."
            odswiezaj = False
        elif {"Tam", "Powrót"} <= {r.kierunek for r in warianty if r.status == StatusRezerwacji.WYBRANA}:
            brakujace = brakujace_elementy_organizacji(s, d)
            oczekiwanie = "Transport został wybrany."
            if brakujace:
                oczekiwanie += " Organizacja oczekuje na: " + ", ".join(brakujace) + "."
            odswiezaj = False
        elif warianty or wynik_transportu:
            porownanie = zestawienie(s, d)
            if not porownanie["mozna_zatwierdzic"] or porownanie["brak_spojnej_preferowanej_pary"]:
                oczekiwanie = "Brak kompletu odpowiednich przejazdów. Podróż wymaga uzgodnienia."
            else:
                oczekiwanie = "Warianty są przygotowane. Oczekiwanie na wybór podróży."
            odswiezaj = False
        else:
            oczekiwanie = "Oczekiwanie na przygotowanie wariantów podróży."
    etap = ETAP_STATUSU_BOCZNEGO.get(d.status, d.status)
    przypomnienie = next((o for o in operacje if o.operacja == OPERACJA_PRZYPOMNIENIA), None)
    przekazanie = next((o for o in operacje if o.operacja == OPERACJA_PRZEKAZANIA), None)
    ostatnia_aktywnosc = max([d.data_zmiany_statusu, *(o.znacznik_czasu for o in operacje)])
    return dict(
        d=d,
        wnioskodawca=s.get(Zolnierz, d.id_zolnierza),
        zatwierdzajacy=s.get(Zolnierz, d.id_zatwierdzajacego) if d.id_zatwierdzajacego else None,
        przedsiewziecie=s.get(Przedsiewziecie, d.id_przedsiewziecia) if d.id_przedsiewziecia else None,
        operacje=operacje,
        decyzje=decyzje,
        agenci={a.id_agenta: a.nazwa for a in s.scalars(select(Agent))},
        bledy=bledy,
        w_toku=odswiezaj,
        oczekiwanie=oczekiwanie,
        etapy=ETAPY_PROCESU,
        pozycja_etapu=ETAPY_PROCESU.index(etap) if etap in ETAPY_PROCESU else -1,
        status_boczny=d.status in ETAP_STATUSU_BOCZNEGO,
        przypomnienie=przypomnienie,
        przekazanie=przekazanie,
        ostatnia_aktywnosc=ostatnia_aktywnosc,
        nieaktualny=(d.status == Status.ZLOZONY or (d.status == Status.ZATWIERDZONY and not wynik_transportu))
        and teraz() - ostatnia_aktywnosc > NIEAKTUALNY_PO,
    )


@router.get("/wnioski/{id_delegacji}", response_class=HTMLResponse)
def wniosek(
    id_delegacji: str,
    request: Request,
    s: Session = Depends(get_sesja),
    uzytkownik: Zolnierz = Depends(biezacy_uzytkownik),
):
    return strona(request, s, "wniosek.html", uzytkownik, **dane_wniosku(s, id_delegacji, uzytkownik))


@router.get("/wnioski/{id_delegacji}/stan", response_class=HTMLResponse)
def stan_wniosku(
    id_delegacji: str,
    request: Request,
    s: Session = Depends(get_sesja),
    uzytkownik: Zolnierz = Depends(biezacy_uzytkownik),
):
    """Fragment strony odświeżany przez HTMX, dopóki agenci pracują nad wnioskiem."""
    return szablony.TemplateResponse(
        request, "_stan_wniosku.html", {**dane_wniosku(s, id_delegacji, uzytkownik), "uzytkownik": uzytkownik}
    )


# ---------------------------------------------------------------- rozliczenie


def dane_rozliczenia(s: Session, id_delegacji: str, uzytkownik: Zolnierz, podglad: bool = False) -> dict:
    """`podglad=True` – sam odczyt: przekazane rozliczenie (delegacja „rozliczona”) też można obejrzeć (-43)."""
    d = s.get(Delegacja, id_delegacji)
    if d is None or d.id_zolnierza != uzytkownik.id_zolnierza:
        raise HTTPException(404, "Nie znaleziono delegacji")
    dozwolone = (Status.DO_ROZLICZENIA, Status.ROZLICZONY) if podglad else (Status.DO_ROZLICZENIA,)
    if d.status not in dozwolone:
        raise HTTPException(409, "Delegacja nie kwalifikuje się do rozliczenia")
    rozliczenie = s.scalar(select(Rozliczenie).where(Rozliczenie.id_delegacji == id_delegacji))
    dokumenty, korekty = [], []
    if rozliczenie is not None:
        dokumenty = s.scalars(
            select(DokumentKosztowy)
            .where(DokumentKosztowy.id_rozliczenia == rozliczenie.id_rozliczenia)
            .order_by(DokumentKosztowy.id_dokumentu)
        ).all()
        korekty = korekty_rozliczenia(s, rozliczenie)
    return {"d": d, "rozliczenie": rozliczenie, "dokumenty": dokumenty, "korekty": korekty}


@router.get("/wnioski/{id_delegacji}/rozliczenie", response_class=HTMLResponse)
def rozliczenie(
    id_delegacji: str,
    request: Request,
    wynik: str | None = None,
    s: Session = Depends(get_sesja),
    uzytkownik: Zolnierz = Depends(biezacy_uzytkownik),
):
    komunikat = {
        WYNIK_DOKUMENT_PRZYJETY: KOMUNIKAT_PRZYJETY,
        WYNIK_PODROZ_ZAPISANA: KOMUNIKAT_PODROZ,
        WYNIK_ZATWIERDZONO: KOMUNIKAT_ZATWIERDZONO,
        WYNIK_WERYFIKACJA_ZLECONA: KOMUNIKAT_WERYFIKACJA_ZLECONA,
        WYNIK_PRZEKAZANIE_ZLECONE: KOMUNIKAT_PRZEKAZANIE_ZLECONE,
    }.get(wynik)
    return strona(
        request,
        s,
        "rozliczenie.html",
        uzytkownik,
        **dane_rozliczenia(s, id_delegacji, uzytkownik, podglad=True),
        komunikat=komunikat,
        wynik=wynik,
    )


WYNIK_PODROZ_ZAPISANA = "podroz-zapisana"
KOMUNIKAT_PODROZ = "Zapisano faktyczny czas podróży i zapewnione świadczenia."


def _liczba_posilkow(tekst: str) -> int | None:
    try:
        liczba = int(tekst or 0)
    except ValueError:
        return None
    return liczba if liczba >= 0 else None


@router.post("/wnioski/{id_delegacji}/rozliczenie/podroz", response_class=HTMLResponse)
async def zapisz_podroz_w_interfejsie(
    id_delegacji: str,
    request: Request,
    faktyczny_wyjazd: str = Form(""),
    faktyczny_powrot: str = Form(""),
    sniadania: str = Form("0"),
    obiady: str = Form("0"),
    kolacje: str = Form("0"),
    bezplatne_zakwaterowanie: bool = Form(False),
    s: Session = Depends(get_sesja),
    broker: Broker = Depends(get_broker),
    uzytkownik: Zolnierz = Depends(biezacy_uzytkownik),
):
    """S22IT1S0P2-60, -61: czas podróży i świadczenia → wyliczenie należności przez Agenta Rozliczeń."""
    kontekst = dane_rozliczenia(s, id_delegacji, uzytkownik)
    try:
        wyjazd, powrot = datetime.fromisoformat(faktyczny_wyjazd), datetime.fromisoformat(faktyczny_powrot)
    except ValueError:
        wyjazd = powrot = None
    posilki = {"śniadanie": sniadania, "obiad": obiady, "kolacja": kolacje}
    liczby = {posilek: _liczba_posilkow(tekst) for posilek, tekst in posilki.items()}
    komunikat = None
    if not mozna_edytowac(kontekst["rozliczenie"]):
        komunikat = KOMUNIKAT_ZATWIERDZONE
    elif wyjazd is None or powrot is None:
        komunikat = "Podaj datę i godzinę wyjazdu oraz powrotu."
    elif powrot <= wyjazd:
        komunikat = "Powrót musi nastąpić po wyjeździe."
    elif None in liczby.values():
        komunikat = "Liczba posiłków musi być liczbą całkowitą, nie mniejszą niż 0."
    if komunikat:
        return strona(request, s, "rozliczenie.html", uzytkownik, **kontekst, komunikat=komunikat, blad=True)

    zapisz_podroz(s, kontekst["d"], wyjazd, powrot, liczby, bezplatne_zakwaterowanie)
    await zlec_wyliczenie(s, broker, kontekst["d"])
    return RedirectResponse(f"/app/wnioski/{id_delegacji}/rozliczenie?wynik={WYNIK_PODROZ_ZAPISANA}", status_code=303)


WYNIK_ZATWIERDZONO = "wyliczenie-zatwierdzone"
KOMUNIKAT_ZATWIERDZONO = "Wyliczenie zatwierdzone – przekaż rozliczenie do weryfikacji."


@router.post("/wnioski/{id_delegacji}/rozliczenie/zatwierdzenie", response_class=HTMLResponse)
async def zatwierdz_wyliczenie_w_interfejsie(
    id_delegacji: str,
    request: Request,
    s: Session = Depends(get_sesja),
    broker: Broker = Depends(get_broker),
    uzytkownik: Zolnierz = Depends(biezacy_uzytkownik),
):
    """S22IT1S0P2-36: delegowany zatwierdza wyliczenie należności – rozliczenie gotowe do weryfikacji."""
    kontekst = dane_rozliczenia(s, id_delegacji, uzytkownik)
    komunikat = await zatwierdz_wyliczenie(s, broker, kontekst["d"])
    if komunikat:
        kontekst = dane_rozliczenia(s, id_delegacji, uzytkownik)
        return strona(request, s, "rozliczenie.html", uzytkownik, **kontekst, komunikat=komunikat, blad=True)
    return RedirectResponse(f"/app/wnioski/{id_delegacji}/rozliczenie?wynik={WYNIK_ZATWIERDZONO}", status_code=303)


@router.post("/wnioski/{id_delegacji}/rozliczenie/dokumenty", response_class=HTMLResponse)
async def dodaj_dokument_w_interfejsie(
    id_delegacji: str,
    request: Request,
    rodzaj: str = Form(...),
    numer: str = Form(...),
    data: str = Form(...),
    kwota: str = Form(...),
    s: Session = Depends(get_sesja),
    broker: Broker = Depends(get_broker),
    uzytkownik: Zolnierz = Depends(biezacy_uzytkownik),
):
    kontekst = dane_rozliczenia(s, id_delegacji, uzytkownik)
    try:
        dane = DokumentKosztowyIn(rodzaj=rodzaj, numer=numer, data=data, kwota=kwota)
    except ValidationError:
        return strona(
            request,
            s,
            "rozliczenie.html",
            uzytkownik,
            **kontekst,
            komunikat="Sprawdź rodzaj, numer, datę i dodatnią kwotę dokumentu.",
            blad=True,
        )
    wynik = przyjmij_dokument(s, kontekst["d"], dane)
    if wynik.przyjeto:
        await zlec_wyliczenie(s, broker, kontekst["d"])
        return RedirectResponse(
            f"/app/wnioski/{id_delegacji}/rozliczenie?wynik={WYNIK_DOKUMENT_PRZYJETY}", status_code=303
        )
    return strona(
        request,
        s,
        "rozliczenie.html",
        uzytkownik,
        **dane_rozliczenia(s, id_delegacji, uzytkownik),
        komunikat=wynik.komunikat,
        blad=not wynik.przyjeto,
    )


# ---------------------------------------------------------------- weryfikacja i przekazanie (S22IT1S0P2-43)

WYNIK_WERYFIKACJA_ZLECONA = "weryfikacja-zlecona"
KOMUNIKAT_WERYFIKACJA_ZLECONA = "Przekazano do weryfikacji – Agent Zgodności sprawdza wyliczenie."
WYNIK_PRZEKAZANIE_ZLECONE = "przekazanie-zlecone"
KOMUNIKAT_PRZEKAZANIE_ZLECONE = "Przekazujemy rozliczenie do komórki finansowej…"


@router.post("/wnioski/{id_delegacji}/rozliczenie/przekazanie", response_class=HTMLResponse)
async def zlec_weryfikacje_w_interfejsie(
    id_delegacji: str,
    request: Request,
    s: Session = Depends(get_sesja),
    broker: Broker = Depends(get_broker),
    uzytkownik: Zolnierz = Depends(biezacy_uzytkownik),
):
    """S22IT1S0P2-43: zatwierdzone wyliczenie trafia do weryfikacji przez Agenta Zgodności."""
    kontekst = dane_rozliczenia(s, id_delegacji, uzytkownik)
    komunikat = await zlec_weryfikacje(s, broker, kontekst["d"])
    if komunikat:
        return strona(request, s, "rozliczenie.html", uzytkownik, **kontekst, komunikat=komunikat, blad=True)
    return RedirectResponse(
        f"/app/wnioski/{id_delegacji}/rozliczenie?wynik={WYNIK_WERYFIKACJA_ZLECONA}", status_code=303
    )


@router.post("/wnioski/{id_delegacji}/rozliczenie/akceptacja", response_class=HTMLResponse)
async def przekaz_rozliczenie_w_interfejsie(
    id_delegacji: str,
    request: Request,
    zastrzezenia: str = Form(""),
    s: Session = Depends(get_sesja),
    broker: Broker = Depends(get_broker),
    uzytkownik: Zolnierz = Depends(biezacy_uzytkownik),
):
    """S22IT1S0P2-43: akceptacja zweryfikowanego wyliczenia albo zastrzeżenia (3a–4a) → komórka finansowa."""
    kontekst = dane_rozliczenia(s, id_delegacji, uzytkownik)
    komunikat = await przekaz_rozliczenie(s, broker, kontekst["d"], zastrzezenia[:1000])
    if komunikat:
        return strona(request, s, "rozliczenie.html", uzytkownik, **kontekst, komunikat=komunikat, blad=True)
    return RedirectResponse(
        f"/app/wnioski/{id_delegacji}/rozliczenie?wynik={WYNIK_PRZEKAZANIE_ZLECONE}", status_code=303
    )


# ---------------------------------------------------------------- przełożony – zbiorcze zatwierdzanie (S22IT1S0P2-35)


def kwota(wartosc) -> str:
    """Kwota w złotych w zapisie polskim, np. 1 670,00 zł."""
    if wartosc is None:
        return "–"
    return f"{wartosc:,.2f}".replace(",", "\u00a0").replace(".", ",") + "\u00a0zł"


szablony.env.filters["kwota"] = kwota


def dane_zatwierdzania(s: Session, uzytkownik: Zolnierz) -> dict:
    """Zestawienie dla przełożonego: wnioski, szacowane koszty, budżet jednostki, wnioski wstrzymane."""
    wnioski = oczekujace_na_decyzje(s, uzytkownik.id_zolnierza)
    return dict(
        wnioski=wnioski,
        wnioskodawcy={z.id_zolnierza: z for z in s.scalars(select(Zolnierz))},
        koszty={d.id_delegacji: koszt_delegacji(d) for d in wnioski},
        budzet=budzet_jednostki(s, uzytkownik),
        wstrzymane=wstrzymane_z_powodu_budzetu(s, [d.id_delegacji for d in wnioski]),
        przekazane_zastepcy=przekazane_zastepcy(s, uzytkownik.id_zolnierza),
    )


def przekazane_zastepcy(s: Session, id_przelozonego: str) -> list[Delegacja]:
    """S22IT1S0P2-13: wnioski podwładnych przekazane po terminie osobie zastępującej, jeszcze bez decyzji."""
    return list(
        s.scalars(
            select(Delegacja)
            .join(Zolnierz, Zolnierz.id_zolnierza == Delegacja.id_zolnierza)
            .where(
                Zolnierz.id_przelozonego == id_przelozonego,
                Delegacja.przekazano_zastepcy.is_(True),
                Delegacja.status == Status.W_AKCEPTACJI,
                Delegacja.id_zatwierdzajacego != id_przelozonego,
            )
            .order_by(Delegacja.data_zmiany_statusu)
        )
    )


@router.get("/do-zatwierdzenia", response_class=HTMLResponse)
def do_zatwierdzenia(
    request: Request, s: Session = Depends(get_sesja), uzytkownik: Zolnierz = Depends(biezacy_uzytkownik)
):
    return strona(request, s, "do_zatwierdzenia.html", uzytkownik, **dane_zatwierdzania(s, uzytkownik))


@router.post("/do-zatwierdzenia", response_class=HTMLResponse)
async def decyduj_zbiorczo(
    request: Request,
    wnioski: Annotated[list[str] | None, Form()] = None,
    decyzja: str = Form(...),
    uzasadnienie: str = Form(""),
    s: Session = Depends(get_sesja),
    broker: Broker = Depends(get_broker),
    uzytkownik: Zolnierz = Depends(biezacy_uzytkownik),
):
    wynik = await decyzja_zbiorcza(s, broker, uzytkownik, wnioski or [], decyzja, uzasadnienie)
    kontekst = dane_zatwierdzania(s, uzytkownik)
    return strona(request, s, "do_zatwierdzenia.html", uzytkownik, **kontekst, wynik=wynik)


@router.post("/do-zatwierdzenia/{id_delegacji}", response_class=HTMLResponse)
async def decyduj(
    id_delegacji: str,
    request: Request,
    decyzja: str = Form(...),
    uzasadnienie: str = Form(""),
    s: Session = Depends(get_sesja),
    broker: Broker = Depends(get_broker),
    uzytkownik: Zolnierz = Depends(biezacy_uzytkownik),
):
    if decyzja not in ("Zgoda", "Odmowa", "Do uzupełnienia"):
        raise HTTPException(400, "Nieznana decyzja")
    d = s.get(Delegacja, id_delegacji)
    if d is None or d.id_zatwierdzajacego != uzytkownik.id_zolnierza:
        raise HTTPException(404, "Nie znaleziono wniosku")
    wnioskodawca = s.get(Zolnierz, d.id_zolnierza)
    if decyzja != "Zgoda" and not uzasadnienie.strip():
        return szablony.TemplateResponse(
            request,
            "_wiersz_decyzji.html",
            {
                "d": d,
                "wnioskodawca": wnioskodawca,
                "blad": "Podaj uzasadnienie odmowy lub zwrotu do uzupełnienia",
                "csrf": token_csrf(request.cookies.get(CIASTECZKO, ""), settings.session_secret.get_secret_value()),
            },
        )
    wynik = await decyzja_zbiorcza(s, broker, uzytkownik, [id_delegacji], decyzja, uzasadnienie)
    kontekst = {
        "d": d,
        "wnioskodawca": wnioskodawca,
        "koszt": koszt_delegacji(d),
        "csrf": token_csrf(request.cookies.get(CIASTECZKO, ""), settings.session_secret.get_secret_value()),
    }
    if wynik.wstrzymane:
        kontekst.update(wstrzymany=True, blad=wynik.komunikat)
    elif wynik.blad:
        kontekst["blad"] = wynik.komunikat
    else:
        kontekst["przekazano"] = decyzja
    return szablony.TemplateResponse(request, "_wiersz_decyzji.html", kontekst)
