"""Aktualizacja planu delegacji po zmianie terminu zadania – S22IT1S0P2-41 (podzadania 157–161).

Obsługę wywołuje Agent Monitorujący (AG-08):
1. Organizator zmienia termin przedsięwzięcia (`POST /przedsiewziecia/{id}/termin`) → dla każdej delegacji,
   której to dotyczy, `zdarzenie.zmiana_terminu` → `przygotuj_nowy_plan` (podzadanie 157):
   - nowe daty delegacji przesunięte tak jak termin zadania (wyjazd o zmianę początku, powrót o zmianę końca),
   - nowy plan transportu: przejazd tam z dojazdem przed rozpoczęciem zajęć (gdy żaden kurs nie zdąży –
     dzień wcześniej), przejazd powrotny w dniu powrotu; preferowany środek transportu, potem `podroze.ocena`,
   - nowy nocleg w limicie (`stawki.LIMIT_NOCLEGOWY`) z terminowym dojazdem – reguły Agenta Zakwaterowania
     (S22IT1S0P2-39); dotychczasowy obiekt ma pierwszeństwo, jeśli nadal spełnia warunki,
   - wzrost kosztu musi mieścić się w dostępnym budżecie jednostki,
   - elementy nowego planu jako `Przejazd`/`Zakwaterowanie` ze statusem `NOWY_PLAN`, porównanie planów
     w `ZmianaTerminu`, zorganizowana delegacja wraca do etapu „zatwierdzony”, powiadomienie delegowanego,
   - brak planu w limitach → `DO_UZGODNIENIA` i przekazanie przełożonemu.
2. Delegowany potwierdza nowy plan albo zgłasza brak możliwości udziału (`/app/wnioski/{id}/nowy-plan`)
   → `decyzja.nowy_plan` → `zapisz_decyzje_planu`:
   - potwierdzenie (podzadanie 158): stare rezerwacje → `ANULOWANA`, nowe → `WYBRANA`, nowe daty delegacji,
     korekta budżetu, aktualizacja polecenia wyjazdu (termin i wpis w `zmiany_terminu`), zmiana zarejestrowana,
     `wynik.transport` – Koordynator znów uzna wyjazd za zorganizowany,
   - brak możliwości udziału (3a–4a, podzadanie 160): nowy plan i nieaktualne rezerwacje anulowane,
     delegacja wstrzymana (`WSTRZYMANA`), sprawa u przełożonego, informacja dla delegowanego.
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
    ObiektZakwaterowania,
    Polaczenie,
    PolecenieWyjazdu,
    Przedsiewziecie,
    Przejazd,
    Status,
    StatusRezerwacji,
    StatusZmianyTerminu,
    Zakwaterowanie,
    ZmianaTerminu,
    Zolnierz,
)
from ..podroze import nazwa_miejscowosci, ocena, przejazdy, przyjazd, zgodny_transport
from ..przynaleznosc import jednostka_delegacji
from ..stawki import LIMIT_NOCLEGOWY
from .base import BladProcesu, Kontekst
from .transport import zablokuj_delegacje
from .zakwaterowanie import kolejnosc, maks_czas_dojazdu, terminowy_dojazd
from .zmiana_przejazdu import dostepny_budzet

# Warunek początkowy: delegacja zaplanowana (przed wyjazdem). W trakcie wyjazdu zmianę obsługuje przełożony.
ETAPY_ZAPLANOWANE = (Status.ZATWIERDZONY, Status.ZORGANIZOWANY)
TYP_PLANU = "Nowy plan po zmianie terminu zadania"
TYP_DECYZJI = "Decyzja delegowanego o nowym planie"


class BrakPlanu(Exception):
    """Nie da się przygotować planu zgodnego z limitami – powód dla przełożonego."""


# ---------------------------------------------------------------- zapytania wspólne dla agenta, API i ekranu


def oczekujaca_zmiana(s: Session, d: Delegacja) -> ZmianaTerminu | None:
    return s.scalar(
        select(ZmianaTerminu).where(
            ZmianaTerminu.id_delegacji == d.id_delegacji, ZmianaTerminu.status == StatusZmianyTerminu.OCZEKUJE
        )
    )


def ostatnia_zmiana(s: Session, d: Delegacja) -> ZmianaTerminu | None:
    return s.scalar(
        select(ZmianaTerminu)
        .where(ZmianaTerminu.id_delegacji == d.id_delegacji)
        .order_by(ZmianaTerminu.id_zmiany.desc())
        .limit(1)
    )


def delegacje_przedsiewziecia(s: Session, id_przedsiewziecia: str) -> list[Delegacja]:
    """Delegacje zaplanowane na przedsięwzięcie – tych dotyczy zmiana jego terminu."""
    return list(
        s.scalars(
            select(Delegacja)
            .where(Delegacja.id_przedsiewziecia == id_przedsiewziecia, Delegacja.status.in_(ETAPY_ZAPLANOWANE))
            .order_by(Delegacja.id_delegacji)
        )
    )


def _rezerwacje(s: Session, d: Delegacja, status: str) -> tuple[list[Przejazd], Zakwaterowanie | None]:
    jazdy = [r for r in przejazdy(s, d) if r.status == status]
    nocleg = s.scalar(
        select(Zakwaterowanie).where(Zakwaterowanie.id_delegacji == d.id_delegacji, Zakwaterowanie.status == status)
    )
    return jazdy, nocleg


def _kwota(x: Decimal | None) -> str:
    return f"{x or Decimal('0'):.2f} zł".replace(".", ",")


def _opis_przejazdu(s: Session, r: Przejazd | None) -> str | None:
    if r is None:
        return None
    p = s.get(Polaczenie, r.id_polaczenia) if r.id_polaczenia else None
    kurs = f"{p.godzina_odjazdu:%H:%M}–{p.godzina_przyjazdu:%H:%M} {p.przewoznik}" if p else r.srodek_transportu
    return f"{r.data_przejazdu:%d.%m.%Y} {kurs} ({_kwota(r.koszt)})"


def _opis_noclegu(s: Session, z: Zakwaterowanie | None) -> str | None:
    if z is None:
        return None
    o = s.get(ObiektZakwaterowania, z.id_obiektu)
    noce = (z.data_do - z.data_od).days
    nazwa = o.nazwa if o else z.id_obiektu
    return f"{nazwa}, {z.data_od:%d.%m}–{z.data_do:%d.%m.%Y}, {noce} noc. ({_kwota(z.koszt_calkowity)})"


def opis_planu(
    s: Session, wyjazd: date | None, powrot: date | None, jazdy: list[Przejazd], nocleg: Zakwaterowanie | None
) -> dict:
    """Plan w postaci do porównania na ekranie: termin, przejazdy, nocleg i łączny koszt."""
    tam = next((r for r in jazdy if r.kierunek == "Tam"), None)
    powr = next((r for r in jazdy if r.kierunek == "Powrót"), None)
    koszt = sum((r.koszt or Decimal("0") for r in jazdy), Decimal("0")) + (
        nocleg.koszt_calkowity if nocleg else Decimal("0")
    )
    return {
        "termin": f"{wyjazd:%d.%m.%Y} – {powrot:%d.%m.%Y}" if wyjazd and powrot else None,
        "tam": _opis_przejazdu(s, tam),
        "powrot": _opis_przejazdu(s, powr),
        "nocleg": _opis_noclegu(s, nocleg),
        "koszt": str(koszt),
    }


# ---------------------------------------------------------------- dobór nowego planu (podzadanie 157)


def _polaczenia(s: Session, skad: str, dokad: str) -> list[Polaczenie]:
    return [
        p
        for p in s.scalars(select(Polaczenie))
        if not p.id_polaczenia.startswith("M-")  # oferty ręczne należą do innych delegacji
        and nazwa_miejscowosci(p.skad) == nazwa_miejscowosci(skad)
        and nazwa_miejscowosci(p.dokad) == nazwa_miejscowosci(dokad)
        and p.czas_min > 0
        and p.koszt >= 0
    ]


def _najlepszy(d: Delegacja, oferty: list[Polaczenie], data: date, deadline: datetime | None) -> Polaczenie | None:
    terminowe = [p for p in oferty if deadline is None or przyjazd(p, data) <= deadline]
    terminowe.sort(key=lambda p: (not zgodny_transport(d, p.srodek_transportu), ocena(p, data, deadline)))
    return terminowe[0] if terminowe else None


def dobierz_przejazdy(
    s: Session, d: Delegacja, wyjazd: date, powrot: date, deadline: datetime | None
) -> tuple[date, Polaczenie, Polaczenie]:
    """Przejazd tam z dojazdem przed zajęciami (w razie potrzeby dzień wcześniej) i przejazd powrotny."""
    jednostka = jednostka_delegacji(s, d)
    baza, cel = (jednostka.miejscowosc if jednostka else ""), (d.miejsce_docelowe or "")
    tam = _najlepszy(d, _polaczenia(s, baza, cel), wyjazd, deadline)
    if tam is None and wyjazd - timedelta(days=1) > teraz().date():
        wyjazd -= timedelta(days=1)
        tam = _najlepszy(d, _polaczenia(s, baza, cel), wyjazd, deadline)
    if tam is None:
        raise BrakPlanu(f"brak przejazdu {baza} → {cel} z dojazdem przed rozpoczęciem zadania")
    z_powrotem = _najlepszy(d, _polaczenia(s, cel, baza), powrot, None)
    if z_powrotem is None:
        raise BrakPlanu(f"brak przejazdu powrotnego {cel} → {baza} w dniu {powrot:%d.%m.%Y}")
    return wyjazd, tam, z_powrotem


def dobierz_nocleg(
    s: Session, d: Delegacja, p: Przedsiewziecie | None, dotychczasowy: Zakwaterowanie | None
) -> ObiektZakwaterowania:
    """Obiekt w limicie z terminowym dojazdem; dotychczasowy obiekt zostaje, jeśli nadal spełnia warunki."""
    miejscowosc = p.miejscowosc if p else d.miejsce_docelowe
    maks_min = maks_czas_dojazdu(p.godzina_rozpoczecia if p else None)
    obiekty = [
        o
        for o in s.scalars(select(ObiektZakwaterowania).where(ObiektZakwaterowania.miejscowosc == miejscowosc))
        if terminowy_dojazd(o, maks_min) and o.cena_za_dobe <= LIMIT_NOCLEGOWY
    ]
    if dotychczasowy is not None:
        ten_sam = next((o for o in obiekty if o.id_obiektu == dotychczasowy.id_obiektu), None)
        if ten_sam is not None:
            return ten_sam  # delegowany ma już tam miejsce – zmieniają się tylko daty
    wolne = sorted((o for o in obiekty if o.liczba_wolnych_miejsc > 0), key=kolejnosc)
    if not wolne:
        raise BrakPlanu(f"brak noclegu w limicie {LIMIT_NOCLEGOWY} zł z terminowym dojazdem ({miejscowosc})")
    return wolne[0]


def _nowe_daty(d: Delegacja, stary_start: date, stary_koniec: date, nowy_start: date, nowy_koniec: date):
    """Daty delegacji przesunięte tak jak termin zadania (względem terminu, do którego pasują obecne daty)."""
    wyjazd = d.data_wyjazdu + (nowy_start - stary_start) if d.data_wyjazdu else nowy_start
    powrot = d.data_powrotu + (nowy_koniec - stary_koniec) if d.data_powrotu else nowy_koniec
    return wyjazd, max(powrot, wyjazd)


def przekaz_przelozonemu(s: Session, d: Delegacja, ktx: Kontekst, powod: str) -> str | None:
    """Sprawa delegacji trafia do przełożonego, delegowany dostaje informację (4a)."""
    wnioskodawca = s.get(Zolnierz, d.id_zolnierza)
    przelozony = d.id_zatwierdzajacego or (wnioskodawca.id_przelozonego if wnioskodawca else None)
    if przelozony:
        ktx.wyslij(Typ.POWIADOMIENIE, {"do": przelozony, "tresc": f"Delegacja {d.id_delegacji}: {powod}"})
    ktx.wyslij(
        Typ.POWIADOMIENIE,
        {
            "do": d.id_zolnierza,
            "tresc": f"Delegacja {d.id_delegacji} wstrzymana po zmianie terminu zadania – sprawę przekazano "
            f"przełożonemu{f' ({przelozony})' if przelozony else ''}",
        },
    )
    return przelozony


def _zastap_poprzednia(s: Session, d: Delegacja) -> None:
    """Kolejna zmiana terminu przed potwierdzeniem – poprzednia propozycja traci ważność."""
    poprzednia = oczekujaca_zmiana(s, d)
    if poprzednia is None:
        return
    poprzednia.status = StatusZmianyTerminu.ZASTAPIONA
    jazdy, nocleg = _rezerwacje(s, d, StatusRezerwacji.NOWY_PLAN)
    for x in [*jazdy, *([nocleg] if nocleg else [])]:
        x.status = StatusRezerwacji.ANULOWANA


def przygotuj_nowy_plan(s: Session, k: Komunikat, ktx: Kontekst) -> None:
    d = zablokuj_delegacje(s, k.id_delegacji)
    if d.status not in ETAPY_ZAPLANOWANE:
        ktx.ostrzezenie(f"Delegacja ma status „{d.status}” – aktualizacja planu dotyczy delegacji przed wyjazdem")
        return
    nowy_start, nowy_koniec = date.fromisoformat(k.dane["nowy_start"]), date.fromisoformat(k.dane["nowy_koniec"])
    biezaca = oczekujaca_zmiana(s, d)
    if biezaca is not None and (biezaca.poczatek_zadania, biezaca.koniec_zadania) == (nowy_start, nowy_koniec):
        ktx.opis = "Nowy plan dla tego terminu został już przygotowany – pominięto ponowienie"
        return
    # daty delegacji pasują do terminu sprzed pierwszej niepotwierdzonej zmiany
    if biezaca is not None:
        przed = (biezaca.poczatek_zadania_przed, biezaca.koniec_zadania_przed)
    else:
        przed = (date.fromisoformat(k.dane["stary_start"]), date.fromisoformat(k.dane["stary_koniec"]))
    wyjazd, powrot = _nowe_daty(d, *przed, nowy_start, nowy_koniec)
    _zastap_poprzednia(s, d)

    stare_jazdy, stary_nocleg = _rezerwacje(s, d, StatusRezerwacji.WYBRANA)
    powod = str(k.dane.get("powod") or "").strip() or None
    zmiana = ZmianaTerminu(
        id_delegacji=d.id_delegacji,
        id_przedsiewziecia=d.id_przedsiewziecia,
        powod=powod,
        poczatek_zadania_przed=przed[0],
        koniec_zadania_przed=przed[1],
        poczatek_zadania=nowy_start,
        koniec_zadania=nowy_koniec,
        stary_wyjazd=d.data_wyjazdu,
        stary_powrot=d.data_powrotu,
        nowy_wyjazd=wyjazd,
        nowy_powrot=powrot,
        stary_plan=opis_planu(s, d.data_wyjazdu, d.data_powrotu, stare_jazdy, stary_nocleg),
        status=StatusZmianyTerminu.OCZEKUJE,
    )
    s.add(zmiana)
    if d.status == Status.ZORGANIZOWANY:
        d.ustaw_status(Status.ZATWIERDZONY)  # dotychczasowy plan jest nieaktualny
    termin_zadania = f"{k.dane['nowy_start']} – {k.dane['nowy_koniec']}"

    p = s.get(Przedsiewziecie, d.id_przedsiewziecia) if d.id_przedsiewziecia else None
    deadline = datetime.combine(p.data_rozpoczecia, p.godzina_rozpoczecia) if p and p.godzina_rozpoczecia else None
    try:
        if wyjazd <= teraz().date():
            raise BrakPlanu("nowy termin wyjazdu już minął albo przypada dziś")
        wyjazd, tam, z_powrotem = dobierz_przejazdy(s, d, wyjazd, powrot, deadline)
        obiekt = dobierz_nocleg(s, d, p, stary_nocleg) if d.potrzeba_zakwaterowania else None
        noce = max(1, (powrot - wyjazd).days)
        nowy_koszt = tam.koszt + z_powrotem.koszt + (obiekt.cena_za_dobe * noce if obiekt else Decimal("0"))
        roznica = nowy_koszt - Decimal(zmiana.stary_plan["koszt"])
        budzet = dostepny_budzet(s, d)
        if budzet is not None and roznica > budzet:
            raise BrakPlanu(f"wzrost kosztu {_kwota(roznica)} przekracza dostępny budżet jednostki {_kwota(budzet)}")
    except BrakPlanu as e:
        zmiana.status = StatusZmianyTerminu.DO_UZGODNIENIA
        zmiana.uzasadnienie = str(e)
        opis = f"Termin zadania zmieniony na {termin_zadania}: nie można przygotować planu w limitach – {e}"
        ktx.ostrzezenie(opis)
        ktx.decyzja(Decyzja(typ=TYP_PLANU, wybrany_wariant="Do uzgodnienia", uzasadnienie=opis, liczba_wariantow=0))
        przekaz_przelozonemu(s, d, ktx, opis + ". Zorganizuj wyjazd indywidualnie.")
        return

    zmiana.nowy_wyjazd = wyjazd
    nowe_jazdy = []
    for kierunek, pol, data in (("Tam", tam, wyjazd), ("Powrót", z_powrotem, powrot)):
        r = Przejazd(
            id_delegacji=d.id_delegacji,
            kierunek=kierunek,
            srodek_transportu=pol.srodek_transportu,
            data_przejazdu=data,
            id_polaczenia=pol.id_polaczenia,
            koszt=pol.koszt,
            koszt_indywidualny=pol.koszt,
            status=StatusRezerwacji.NOWY_PLAN,
            rekomendowany=True,
            uzasadnienie=f"Nowy plan po zmianie terminu zadania na {termin_zadania}",
        )
        s.add(r)
        nowe_jazdy.append(r)
    nowy_nocleg = None
    if obiekt is not None:
        nowy_nocleg = Zakwaterowanie(
            id_delegacji=d.id_delegacji,
            id_obiektu=obiekt.id_obiektu,
            data_od=wyjazd,
            data_do=powrot,
            cena_za_dobe=obiekt.cena_za_dobe,
            koszt_calkowity=obiekt.cena_za_dobe * noce,
            czas_dojazdu_min=obiekt.czas_dojazdu_min,
            status=StatusRezerwacji.NOWY_PLAN,
            uzasadnienie=f"Nowy plan po zmianie terminu zadania na {termin_zadania}",
        )
        s.add(nowy_nocleg)
    s.flush()
    zmiana.nowy_plan = opis_planu(s, wyjazd, powrot, nowe_jazdy, nowy_nocleg)
    zmiana.roznica_kosztu = roznica

    ktx.opis = (
        f"Termin zadania: {termin_zadania}; nowy plan {zmiana.nowy_plan['termin']}, "
        f"zmiana kosztu {roznica:+.2f} zł – oczekuje na potwierdzenie delegowanego"
    )
    ktx.decyzja(
        Decyzja(
            typ=TYP_PLANU,
            wybrany_wariant=f"Tam {tam.id_polaczenia}, powrót {z_powrotem.id_polaczenia}"
            + (f", nocleg {obiekt.id_obiektu}" if obiekt else ""),
            uzasadnienie="Daty przesunięte jak termin zadania; dojazd przed rozpoczęciem zajęć, preferowany środek "
            f"transportu, nocleg w limicie {LIMIT_NOCLEGOWY} zł z terminowym dojazdem, koszt w budżecie jednostki"
            + (f". Powód zmiany: {powod}" if powod else ""),
            liczba_wariantow=1,
            decyzja_finansowa=roznica > 0,
        )
    )
    ktx.wyslij(
        Typ.POWIADOMIENIE,
        {
            "do": d.id_zolnierza,
            "tresc": f"Termin zadania delegacji {d.id_delegacji} zmienił się na {termin_zadania}. "
            "Porównaj dotychczasowy i nowy plan podróży i potwierdź go.",
        },
    )


# ---------------------------------------------------------------- decyzja delegowanego (podzadania 158, 160)


def _aktualizuj_polecenie(s: Session, d: Delegacja, wpis: dict) -> bool:
    polecenie = s.scalar(select(PolecenieWyjazdu).where(PolecenieWyjazdu.id_delegacji == d.id_delegacji))
    if polecenie is None:
        return False
    tresc = dict(polecenie.tresc or {})  # nowy słownik – zmiana kolumny JSON musi być widoczna dla sesji
    tresc["termin"] = {"od": d.data_wyjazdu.isoformat(), "do": d.data_powrotu.isoformat()}
    tresc["zmiany_terminu"] = [*tresc.get("zmiany_terminu", []), wpis]
    polecenie.tresc = tresc
    return True


def zapisz_decyzje_planu(s: Session, k: Komunikat, ktx: Kontekst) -> None:
    d = zablokuj_delegacje(s, k.id_delegacji)
    if k.dane.get("id_osoby") != d.id_zolnierza:
        raise BladProcesu("O nowym planie decyduje delegowany")
    zmiana = oczekujaca_zmiana(s, d)
    if zmiana is None:
        ktx.opis = "Brak planu oczekującego na decyzję – decyzja została już zapisana"
        return
    stare_jazdy, stary_nocleg = _rezerwacje(s, d, StatusRezerwacji.WYBRANA)
    nowe_jazdy, nowy_nocleg = _rezerwacje(s, d, StatusRezerwacji.NOWY_PLAN)
    stare = [*stare_jazdy, *([stary_nocleg] if stary_nocleg else [])]
    nowe = [*nowe_jazdy, *([nowy_nocleg] if nowy_nocleg else [])]
    uzasadnienie = str(k.dane.get("uzasadnienie") or "").strip()
    zmiana.data_decyzji = teraz()

    if not k.dane.get("potwierdz"):
        # 3a–4a: delegowany nie może wziąć udziału w zadaniu w nowym terminie
        if not uzasadnienie:
            raise BladProcesu("Podaj przyczynę braku możliwości udziału w nowym terminie")
        for x in nowe:
            x.status = StatusRezerwacji.ANULOWANA
        for x in stare:  # rezerwacje na stary termin są nieaktualne – delegacja nie zostanie zorganizowana
            x.status = StatusRezerwacji.ANULOWANA
            if isinstance(x, Przejazd):
                x.powod_zmiany = f"Anulowano po zmianie terminu zadania (zmiana #{zmiana.id_zmiany})"
        zmiana.status = StatusZmianyTerminu.WSTRZYMANA
        zmiana.uzasadnienie = uzasadnienie
        opis = f"Delegowany nie może wziąć udziału w zadaniu w nowym terminie: {uzasadnienie}"
        ktx.ostrzezenie(opis + " – realizację wstrzymano, sprawę przekazano przełożonemu")
        ktx.decyzja(Decyzja(typ=TYP_DECYZJI, wybrany_wariant="Brak możliwości udziału", uzasadnienie=opis))
        przekaz_przelozonemu(s, d, ktx, opis + ". Zdecyduj o dalszej realizacji delegacji.")
        return

    if not nowe_jazdy:
        raise BladProcesu("Nowy plan nie ma przejazdów do potwierdzenia")
    # podzadanie 158: anulowanie nieaktualnych rezerwacji i rezerwacja nowego planu
    for x in stare:
        x.status = StatusRezerwacji.ANULOWANA
        if isinstance(x, Przejazd):
            x.powod_zmiany = f"Zastąpiono nowym planem po zmianie terminu zadania (zmiana #{zmiana.id_zmiany})"
    for x in nowe:
        x.status = StatusRezerwacji.WYBRANA
    if d.pierwotna_data_powrotu is None and d.data_powrotu != zmiana.nowy_powrot:
        d.pierwotna_data_powrotu = d.data_powrotu
    d.data_wyjazdu, d.data_powrotu = zmiana.nowy_wyjazd, zmiana.nowy_powrot

    zmiany = [f"termin {zmiana.nowy_plan['termin']}"]
    roznica = zmiana.roznica_kosztu or Decimal("0")
    jednostka = jednostka_delegacji(s, d)
    if jednostka is not None and roznica:
        jednostka.budzet_wykorzystany = max(Decimal("0"), (jednostka.budzet_wykorzystany or Decimal("0")) + roznica)
        zmiany.append(f"budżet jednostki {roznica:+.2f} zł")
    if _aktualizuj_polecenie(
        s,
        d,
        {
            "data": teraz().isoformat(timespec="minutes"),
            "stary_termin": zmiana.stary_plan.get("termin"),
            "nowy_termin": zmiana.nowy_plan["termin"],
            "powod": zmiana.powod,
        },
    ):
        zmiany.append("polecenie wyjazdu zaktualizowane")
    zmiana.status = StatusZmianyTerminu.POTWIERDZONA
    zmiana.uzasadnienie = uzasadnienie or None

    ktx.opis = "Potwierdzono nowy plan: " + "; ".join(zmiany)
    ktx.decyzja(
        Decyzja(
            typ=TYP_DECYZJI,
            wybrany_wariant="Potwierdzono nowy plan",
            uzasadnienie=f"Osoba: {k.dane['id_osoby']}; {ktx.opis}"
            + (f"; uwagi: {uzasadnienie}" if uzasadnienie else ""),
            decyzja_finansowa=roznica > 0,
        )
    )
    ktx.wyslij(
        Typ.POWIADOMIENIE,
        {"do": d.id_zolnierza, "tresc": f"Potwierdzenie zmiany planu delegacji {d.id_delegacji}: " + "; ".join(zmiany)},
    )
    ktx.wyslij(Typ.WYNIK_TRANSPORTU)  # Koordynator sprawdzi, czy wyjazd jest znów zorganizowany
