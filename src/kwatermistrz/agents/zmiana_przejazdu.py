"""Zmiana przejazdu po jego odwołaniu przez przewoźnika – S22IT1S0P2-17 (podzadania 134, 135, 137).

Obsługę wywołuje Agent Monitorujący (AG-08):
1. `zdarzenie.odwolanie_przejazdu` (komunikat przewoźnika przyjęty przez API) → `zarejestruj_odwolanie`:
   - zarezerwowany przejazd (`WYBRANA`) dostaje status `ODWOLANA`, a zorganizowana delegacja wraca
     do etapu „zatwierdzony” (organizacja znów jest niekompletna),
   - system szuka przejazdów zastępczych na tej samej trasie: dojazd przed rozpoczęciem przedsięwzięcia,
     powrót nie wcześniej niż po dojeździe i rozpoczęciu zadania, koszt zmiany mieszczący się w budżecie jednostki;
     gdy żaden kurs w dniu wyjazdu nie zdąży, także dzień wcześniej (z dodatkową dobą noclegu),
   - propozycje zapisane jako `Przejazd` ze statusem `ALTERNATYWA` (najlepsza z `rekomendowany=True`),
     powiadomienie delegowanego; brak propozycji → sprawa trafia do osoby odpowiedzialnej (4a).
2. `wybor.przejazd_alternatywny` (delegowany na ekranie `/app/wnioski/{id}/przejazd-alternatywny`)
   → `zapisz_wybor_alternatywy`:
   - wybór: odwołana rezerwacja i niewybrane propozycje → `ANULOWANA`, nowy przejazd → `WYBRANA`,
     korekta zaangażowanego budżetu, przesunięcie początku noclegu i daty wyjazdu przy wcześniejszym dojeździe,
     wpis zmiany w poleceniu wyjazdu, potwierdzenie dla delegowanego, `wynik.transport` dla Koordynatora,
   - odrzucenie wszystkich (3a–4a): propozycje → `DO_UZGODNIENIA`, przekazanie osobie odpowiedzialnej
     za organizację delegacji i informacja dla delegowanego.
"""

from datetime import date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..audit import Decyzja
from ..db import teraz
from ..messaging import Komunikat, Typ
from ..models import (
    Delegacja,
    Polaczenie,
    PolecenieWyjazdu,
    Przejazd,
    Status,
    StatusRezerwacji,
    Zakwaterowanie,
    Zolnierz,
)
from ..podroze import nazwa_miejscowosci, ocena, przejazdy, przyjazd, termin
from ..przynaleznosc import jednostka_delegacji
from .base import BladProcesu, Kontekst
from .transport import zablokuj_delegacje

# Podróż jeszcze się nie rozpoczęła (warunek początkowy historyjki)
ETAPY_PRZED_PODROZA = (Status.ZATWIERDZONY, Status.ZORGANIZOWANY)
TYP_PROPOZYCJI = "Propozycja przejazdu alternatywnego"
TYP_WYBORU = "Wybór przejazdu alternatywnego"


# ---------------------------------------------------------------- zapytania wspólne dla agenta, API i ekranu


def odwolane_przejazdy(s: Session, d: Delegacja) -> list[Przejazd]:
    """Odwołane rezerwacje, które nie zostały jeszcze zastąpione nowym przejazdem."""
    return [r for r in przejazdy(s, d) if r.status == StatusRezerwacji.ODWOLANA]


def przejazdy_alternatywne(s: Session, d: Delegacja) -> list[Przejazd]:
    """Propozycje zastępcze czekające na wybór delegowanego – rekomendowana jako pierwsza."""
    propozycje = [r for r in przejazdy(s, d) if r.status == StatusRezerwacji.ALTERNATYWA]
    return sorted(propozycje, key=lambda r: (not r.rekomendowany, r.id_przejazdu))


def dostepny_budzet(s: Session, d: Delegacja) -> Decimal | None:
    """Budżet jednostki do wykorzystania: przyznany − zaangażowany − wydatkowany (None = brak danych)."""
    j = jednostka_delegacji(s, d)
    if j is None:
        return None
    return (
        (j.budzet_delegacji or Decimal("0"))
        - (j.budzet_wykorzystany or Decimal("0"))
        - (j.budzet_wydatkowany or Decimal("0"))
    )


def wybrany_nocleg(s: Session, d: Delegacja) -> Zakwaterowanie | None:
    return s.scalar(
        select(Zakwaterowanie).where(
            Zakwaterowanie.id_delegacji == d.id_delegacji,
            Zakwaterowanie.status == StatusRezerwacji.WYBRANA,
        )
    )


def koszt_zmiany(s: Session, d: Delegacja, odwolany: Przejazd, polaczenie: Polaczenie, data: date) -> Decimal:
    """O ile zmiana podnosi koszt delegacji: różnica cen biletów i ewentualne dodatkowe doby noclegu."""
    roznica = polaczenie.koszt - (odwolany.koszt or Decimal("0"))
    nocleg = wybrany_nocleg(s, d)
    if odwolany.kierunek == "Tam" and nocleg is not None and data < nocleg.data_od:
        roznica += nocleg.cena_za_dobe * (nocleg.data_od - data).days
    return roznica


def _poprawne(p: Polaczenie) -> bool:
    return (
        p.czas_min > 0
        and p.koszt >= 0
        and 0 <= p.punktualnosc <= 1
        and 1 <= p.ocena_bezpieczenstwa <= 5
        and p.liczba_przesiadek >= 0
    )


def _trasa(s: Session, d: Delegacja, odwolany: Przejazd) -> tuple[str, str]:
    stare = s.get(Polaczenie, odwolany.id_polaczenia) if odwolany.id_polaczenia else None
    if stare is not None:
        return stare.skad, stare.dokad
    jednostka = jednostka_delegacji(s, d)
    baza, cel = (jednostka.miejscowosc if jednostka else ""), (d.miejsce_docelowe or "")
    return (baza, cel) if odwolany.kierunek == "Tam" else (cel, baza)


def _najwczesniejszy_powrot(s: Session, d: Delegacja) -> datetime | None:
    """Powrót nie może ruszyć przed dojazdem na miejsce i rozpoczęciem zadania."""
    tam = next((r for r in przejazdy(s, d) if r.kierunek == "Tam" and r.status == StatusRezerwacji.WYBRANA), None)
    p_tam = s.get(Polaczenie, tam.id_polaczenia) if tam and tam.id_polaczenia else None
    granice = [g for g in (przyjazd(p_tam, tam.data_przejazdu) if p_tam else None, termin(s, d)) if g]
    return max(granice) if granice else None


def znajdz_alternatywy(s: Session, d: Delegacja, odwolany: Przejazd) -> list[tuple[Polaczenie, date]]:
    """Przejazdy zastępcze na tej samej trasie, terminowe i mieszczące się w budżecie – od najlepszego."""
    skad, dokad = _trasa(s, d, odwolany)
    deadline = termin(s, d) if odwolany.kierunek == "Tam" else None
    powrot_od = _najwczesniejszy_powrot(s, d) if odwolany.kierunek == "Powrót" else None
    polaczenia = [
        p
        for p in s.scalars(select(Polaczenie))
        if p.id_polaczenia != odwolany.id_polaczenia
        and not p.id_polaczenia.startswith("M-")  # oferty ręczne należą do innych delegacji
        and nazwa_miejscowosci(p.skad) == nazwa_miejscowosci(skad)
        and nazwa_miejscowosci(p.dokad) == nazwa_miejscowosci(dokad)
        and _poprawne(p)
    ]

    def kandydaci(data: date) -> list[tuple[Polaczenie, date]]:
        wynik = []
        for p in polaczenia:
            if deadline is not None and przyjazd(p, data) > deadline:
                continue
            if powrot_od is not None and datetime.combine(data, p.godzina_odjazdu) < powrot_od:
                continue
            wynik.append((p, data))
        return wynik

    oferty = kandydaci(odwolany.data_przejazdu)
    dzien_wczesniej = odwolany.data_przejazdu - timedelta(days=1)
    if not oferty and odwolany.kierunek == "Tam" and dzien_wczesniej > teraz().date():
        oferty = kandydaci(dzien_wczesniej)
    budzet = dostepny_budzet(s, d)
    if budzet is not None:
        oferty = [(p, data) for p, data in oferty if koszt_zmiany(s, d, odwolany, p, data) <= budzet]
    return sorted(oferty, key=lambda o: ocena(o[0], o[1], deadline))


def przekaz_odpowiedzialnemu(s: Session, d: Delegacja, ktx: Kontekst, powod: str) -> str | None:
    """Scenariusz 4a: osoba odpowiedzialna za organizację delegacji przejmuje sprawę przejazdu."""
    wnioskodawca = s.get(Zolnierz, d.id_zolnierza)
    odpowiedzialny = d.id_zatwierdzajacego or (wnioskodawca.id_przelozonego if wnioskodawca else None)
    if odpowiedzialny:
        ktx.wyslij(
            Typ.POWIADOMIENIE,
            {"do": odpowiedzialny, "tresc": f"Delegacja {d.id_delegacji}: {powod} – zorganizuj przejazd indywidualnie"},
        )
    tresc = f"Sprawę przejazdu delegacji {d.id_delegacji} przekazano osobie odpowiedzialnej za organizację"
    ktx.wyslij(
        Typ.POWIADOMIENIE, {"do": d.id_zolnierza, "tresc": tresc + (f" ({odpowiedzialny})" if odpowiedzialny else "")}
    )
    return odpowiedzialny


def _opis(r: Przejazd, p: Polaczenie | None) -> str:
    kurs = f"{p.przewoznik} {p.godzina_odjazdu:%H:%M}" if p else r.srodek_transportu
    return f"{r.kierunek} {r.data_przejazdu:%d.%m.%Y}: {kurs}"


# ---------------------------------------------------------------- 1. odwołanie przejazdu (podzadanie 134)


def zarejestruj_odwolanie(s: Session, k: Komunikat, ktx: Kontekst) -> None:
    d = zablokuj_delegacje(s, k.id_delegacji)
    r = s.get(Przejazd, k.dane.get("id_przejazdu"))
    if r is None or r.id_delegacji != d.id_delegacji:
        raise BladProcesu("Nie znaleziono przejazdu tej delegacji")
    if r.status in (StatusRezerwacji.ODWOLANA, StatusRezerwacji.ANULOWANA):
        ktx.opis = "Odwołanie tego przejazdu zostało już odnotowane – pominięto ponowienie"
        return
    if r.status != StatusRezerwacji.WYBRANA:
        ktx.ostrzezenie(f"Przejazd ma status „{r.status}” – odwołanie dotyczy tylko zarezerwowanego przejazdu")
        return
    if d.status not in ETAPY_PRZED_PODROZA or r.data_przejazdu < teraz().date():
        ktx.ostrzezenie("Podróż już się rozpoczęła – odwołanie wymaga obsługi w trakcie wyjazdu")
        return

    przyczyna = str(k.dane.get("przyczyna") or "").strip() or "przewoźnik nie podał przyczyny"
    stare = s.get(Polaczenie, r.id_polaczenia) if r.id_polaczenia else None
    r.status = StatusRezerwacji.ODWOLANA
    r.powod_zmiany = f"Odwołany przez przewoźnika: {przyczyna}"
    if d.status == Status.ZORGANIZOWANY:
        d.ustaw_status(Status.ZATWIERDZONY)  # organizacja wyjazdu znów jest niekompletna

    oferty = znajdz_alternatywy(s, d, r)
    for nr, (p, data) in enumerate(oferty):
        zmiana = koszt_zmiany(s, d, r, p, data)
        uwagi = [f"przyjazd {przyjazd(p, data):%d.%m %H:%M}"]
        if data != r.data_przejazdu:
            uwagi.append("wyjazd dzień wcześniej z dodatkową dobą noclegu")
        uwagi.append(f"zmiana kosztu {zmiana:+.2f} zł")
        s.add(
            Przejazd(
                id_delegacji=d.id_delegacji,
                kierunek=r.kierunek,
                srodek_transportu=p.srodek_transportu,
                data_przejazdu=data,
                id_polaczenia=p.id_polaczenia,
                koszt=p.koszt,
                koszt_indywidualny=p.koszt,
                status=StatusRezerwacji.ALTERNATYWA,
                rekomendowany=nr == 0,
                uzasadnienie=("Rekomendowany zamiennik: " if nr == 0 else "Zamiennik: ") + ", ".join(uwagi) + ".",
                powod_zmiany=f"Zamiennik odwołanego przejazdu #{r.id_przejazdu}",
            )
        )
    s.flush()

    odwolany = _opis(r, stare)
    ktx.decyzja(
        Decyzja(
            typ=TYP_PROPOZYCJI,
            wybrany_wariant=f"{r.kierunek}: {oferty[0][0].id_polaczenia}" if oferty else "Do uzgodnienia",
            liczba_wariantow=len(oferty),
            uzasadnienie=f"Odwołano: {odwolany} ({przyczyna}). Kryteria: dojazd przed rozpoczęciem zadania, "
            "budżet jednostki; następnie koszt, bezpieczeństwo, przesiadki, punktualność i czas.",
            decyzja_finansowa=True,
        )
    )
    if not oferty:
        ktx.ostrzezenie(f"Odwołano {odwolany}; brak przejazdu zastępczego z terminowym dojazdem w budżecie")
        przekaz_odpowiedzialnemu(s, d, ktx, f"przewoźnik odwołał przejazd ({odwolany}), brak zamiennika")
        return
    ktx.opis = f"Odwołano {odwolany}; propozycje zastępcze: {len(oferty)}"
    ktx.wyslij(
        Typ.POWIADOMIENIE,
        {
            "do": d.id_zolnierza,
            "tresc": f"Przewoźnik odwołał przejazd {odwolany} (delegacja {d.id_delegacji}, przyczyna: {przyczyna}). "
            f"Wybierz przejazd alternatywny – przygotowano propozycji: {len(oferty)}.",
        },
    )


# ---------------------------------------------------------------- 2. wybór lub odrzucenie zamiennika (135, 137)


def _dopisz_do_polecenia(s: Session, d: Delegacja, zmiana: dict) -> bool:
    polecenie = s.scalar(select(PolecenieWyjazdu).where(PolecenieWyjazdu.id_delegacji == d.id_delegacji))
    if polecenie is None:
        return False
    tresc = dict(polecenie.tresc or {})  # nowy słownik – zmiana kolumny JSON musi być widoczna dla sesji
    tresc["termin"] = {
        "od": d.data_wyjazdu.isoformat() if d.data_wyjazdu else None,
        "do": d.data_powrotu.isoformat() if d.data_powrotu else None,
    }
    tresc["zmiany_przejazdu"] = [*tresc.get("zmiany_przejazdu", []), zmiana]
    polecenie.tresc = tresc
    return True


def zapisz_wybor_alternatywy(s: Session, k: Komunikat, ktx: Kontekst) -> None:
    d = zablokuj_delegacje(s, k.id_delegacji)
    if k.dane.get("id_osoby") != d.id_zolnierza:
        raise BladProcesu("Przejazd alternatywny wybiera delegowany")
    propozycje = przejazdy_alternatywne(s, d)
    if not propozycje:
        ktx.opis = "Brak propozycji oczekujących na wybór – decyzja została już zapisana"
        return
    uzasadnienie = str(k.dane.get("uzasadnienie") or "").strip()

    if k.dane.get("odrzuc_wszystkie"):
        if not uzasadnienie:
            raise BladProcesu("Podaj przyczynę odrzucenia wszystkich przejazdów alternatywnych")
        for p in propozycje:
            p.status = StatusRezerwacji.DO_UZGODNIENIA
            p.uzasadnienie = uzasadnienie
        ktx.decyzja(
            Decyzja(
                typ=TYP_WYBORU,
                wybrany_wariant="Do uzgodnienia",
                liczba_wariantow=len(propozycje),
                uzasadnienie=f"Delegowany odrzucił wszystkie propozycje: {uzasadnienie}",
            )
        )
        odpowiedzialny = przekaz_odpowiedzialnemu(
            s, d, ktx, f"delegowany odrzucił przejazdy alternatywne ({uzasadnienie})"
        )
        ktx.ostrzezenie(f"Odrzucono przejazdy alternatywne – sprawę przekazano: {odpowiedzialny or 'brak osoby'}")
        return

    nowy = next((p for p in propozycje if p.id_przejazdu == k.dane.get("id_przejazdu")), None)
    if nowy is None:
        raise BladProcesu("Wybierz jeden z przedstawionych przejazdów alternatywnych")
    stary = next((r for r in odwolane_przejazdy(s, d) if r.kierunek == nowy.kierunek), None)
    polaczenie = s.get(Polaczenie, nowy.id_polaczenia) if nowy.id_polaczenia else None
    koszt = (
        koszt_zmiany(s, d, stary, polaczenie, nowy.data_przejazdu)
        if stary is not None and polaczenie is not None
        else Decimal("0")
    )
    budzet = dostepny_budzet(s, d)
    if budzet is not None and koszt > budzet:
        raise BladProcesu("Wybrany przejazd przekracza dostępny budżet jednostki")

    # podzadanie 135: anulowanie dotychczasowej rezerwacji i rezerwacja nowego przejazdu
    if stary is not None:
        stary.status = StatusRezerwacji.ANULOWANA
        stary.powod_zmiany = (
            f"{stary.powod_zmiany or ''}\nRezerwacja anulowana – zastąpiona przejazdem #{nowy.id_przejazdu}"
        )
    for p in propozycje:
        if p is not nowy:
            p.status = StatusRezerwacji.ANULOWANA
            p.uzasadnienie = "Nie wybrano – delegowany wybrał inny przejazd zastępczy"
    nowy.status = StatusRezerwacji.WYBRANA
    nowy.uzasadnienie = uzasadnienie or "Wybrany przez delegowanego po odwołaniu przejazdu"

    zmiany = [f"nowy przejazd {_opis(nowy, polaczenie)}"]
    jednostka = jednostka_delegacji(s, d)
    if jednostka is not None and koszt:
        jednostka.budzet_wykorzystany = max(Decimal("0"), (jednostka.budzet_wykorzystany or Decimal("0")) + koszt)
        zmiany.append(f"budżet jednostki {koszt:+.2f} zł")
    nocleg = wybrany_nocleg(s, d)
    if nowy.kierunek == "Tam" and nocleg is not None and nowy.data_przejazdu < nocleg.data_od:
        nocleg.data_od = nowy.data_przejazdu
        nocleg.koszt_calkowity = nocleg.cena_za_dobe * max(1, (nocleg.data_do - nocleg.data_od).days)
        zmiany.append(f"nocleg od {nocleg.data_od:%d.%m.%Y}")
    if nowy.kierunek == "Tam" and d.data_wyjazdu and nowy.data_przejazdu < d.data_wyjazdu:
        d.data_wyjazdu = nowy.data_przejazdu
        zmiany.append(f"wyjazd {d.data_wyjazdu:%d.%m.%Y}")
    if _dopisz_do_polecenia(
        s,
        d,
        {
            "data": teraz().isoformat(timespec="minutes"),
            "odwolany": _opis(stary, s.get(Polaczenie, stary.id_polaczenia) if stary.id_polaczenia else None)
            if stary
            else None,
            "nowy": _opis(nowy, polaczenie),
            "przyczyna": (stary.powod_zmiany or "").splitlines()[0] if stary else None,
        },
    ):
        zmiany.append("polecenie wyjazdu zaktualizowane")

    ktx.opis = "Zmiana przejazdu: " + "; ".join(zmiany)
    ktx.decyzja(
        Decyzja(
            typ=TYP_WYBORU,
            wybrany_wariant=f"{nowy.kierunek}: {nowy.id_polaczenia}",
            liczba_wariantow=len(propozycje),
            uzasadnienie=f"Osoba: {k.dane['id_osoby']}; " + (uzasadnienie or "wybór zamiennika") + "; " + ktx.opis,
            decyzja_finansowa=koszt > 0,
        )
    )
    ktx.wyslij(
        Typ.POWIADOMIENIE,
        {
            "do": d.id_zolnierza,
            "tresc": f"Potwierdzenie zmiany przejazdu delegacji {d.id_delegacji}: " + "; ".join(zmiany),
        },
    )
    ktx.wyslij(Typ.WYNIK_TRANSPORTU)  # Koordynator sprawdzi, czy wyjazd jest znów zorganizowany
