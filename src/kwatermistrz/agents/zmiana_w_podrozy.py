"""Powiadomienie delegowanego o zmianie w podróży wraz z rozwiązaniem zastępczym – S22IT1S0P2-34 (podzadania 147–152).

Obsługę wywołuje Agent Monitorujący (AG-08):
1. Dostawca usługi (przewoźnik albo obiekt noclegowy) zgłasza zmianę: `POST /zmiany-uslug` – API identyfikuje
   delegacje w trakcie realizacji, których rezerwacje obejmuje zmiana (podzadanie 147), i dla każdej publikuje
   `zdarzenie.zmiana_uslugi` → `przygotuj_rozwiazanie` (podzadanie 148):
   - usługa dostaje status `ZAKLOCONA`, a system wybiera JEDNO rozwiązanie zastępcze zgodne z warunkami delegacji:
     przejazd na tej samej trasie i w tym samym dniu, odjeżdżający jeszcze przed nami, albo obiekt w miejscowości
     zadania z wolnymi miejscami, dojazdem na czas i ceną w limicie noclegowym – w obu przypadkach z kosztem zmiany
     mieszczącym się w budżecie jednostki,
   - rozwiązanie zapisane ze statusem `ZASTEPCZA`, a w historii delegacji (`ZmianaUslugi`) zdarzenie, koszt,
     zgodność z limitem i termin odpowiedzi; delegowany dostaje powiadomienie (podzadanie 149),
   - opóźnienie przejazdu, które nie ma lepszego zamiennika, zmienia tylko historię (`BEZ_ZMIAN`) – delegowany
     dostaje informację; brak rozwiązania → sprawa u osoby dyżurnej jednostki.
2. Delegowany akceptuje albo odrzuca rozwiązanie (`/app/wnioski/{id}/zmiana-w-podrozy`) → `decyzja.zmiana_uslugi`
   → `zapisz_odpowiedz`:
   - akceptacja (podzadanie 150): stara rezerwacja `ANULOWANA`, zastępcza `WYBRANA`, korekta budżetu jednostki,
     wpis w poleceniu wyjazdu (`zmiany_w_podrozy`, przy noclegu także sekcja `zakwaterowanie`), potwierdzenie dla
     delegowanego i informacja dla przełożonego,
   - odrzucenie: sprawa u osoby dyżurnej.
3. Brak odpowiedzi w terminie (3a–4a, podzadanie 151): cykl Agenta Monitorującego (`zmiany_po_terminie`)
   → `przekaz_po_terminie` – sprawa u osoby dyżurnej jednostki, informacja dla delegowanego.
"""

from datetime import date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

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
    StatusZmianyUslugi,
    Zakwaterowanie,
    ZmianaUslugi,
    Zolnierz,
)
from ..podroze import nazwa_miejscowosci, ocena, przyjazd, termin
from ..przynaleznosc import jednostka_delegacji
from ..stawki import LIMIT_NOCLEGOWY
from .base import BladProcesu, Kontekst, pobierz_delegacje
from .zakwaterowanie import kolejnosc, maks_czas_dojazdu, terminowy_dojazd
from .zmiana_przejazdu import _poprawne, _trasa, dostepny_budzet

USLUGI = ("transport", "zakwaterowanie")
RODZAJE_ZDARZEN = ("odwołanie", "opóźnienie", "zmiana terminu", "brak dostępności")
CZAS_NA_ODPOWIEDZ = timedelta(hours=2)  # termin odpowiedzi delegowanego na rozwiązanie zastępcze
ZAPAS_NA_DOJSCIE = timedelta(minutes=30)  # przejazd zastępczy musi odjeżdżać najwcześniej za tyle
KONTROLA_ODPOWIEDZI = "kontrola.odpowiedz_na_zmiane"  # wewnętrzny typ zadania cyklicznego
TYP_ROZWIAZANIA = "Rozwiązanie zastępcze w podróży"
TYP_ODPOWIEDZI = "Odpowiedź delegowanego na zmianę w podróży"


# ---------------------------------------------------------------- zapytania wspólne dla agenta, API i ekranu


def zmiany_uslug(s: Session, d: Delegacja) -> list[ZmianaUslugi]:
    """Historia zmian w podróży – od najnowszej."""
    return list(
        s.scalars(
            select(ZmianaUslugi)
            .where(ZmianaUslugi.id_delegacji == d.id_delegacji)
            .order_by(ZmianaUslugi.id_zmiany.desc())
        )
    )


def oczekujaca_zmiana(s: Session, d: Delegacja) -> ZmianaUslugi | None:
    return next((z for z in zmiany_uslug(s, d) if z.status == StatusZmianyUslugi.OCZEKUJE), None)


def osoba_dyzurna(s: Session, d: Delegacja) -> str | None:
    """Osoba dyżurna jednostki delegowanego; bez wpisu w ewidencji – przełożony delegowanego."""
    jednostka = jednostka_delegacji(s, d)
    if jednostka is not None and jednostka.id_dyzurnego:
        return jednostka.id_dyzurnego
    wnioskodawca = s.get(Zolnierz, d.id_zolnierza)
    return wnioskodawca.id_przelozonego if wnioskodawca else None


def przelozony_delegowanego(s: Session, d: Delegacja) -> str | None:
    wnioskodawca = s.get(Zolnierz, d.id_zolnierza)
    return (wnioskodawca.id_przelozonego if wnioskodawca else None) or d.id_zatwierdzajacego


def identyfikuj_delegacje(
    s: Session, usluga: str, data: date, id_polaczenia: str | None = None, id_obiektu: str | None = None
) -> list[tuple[str, int]]:
    """Podzadanie 147: delegacje w trakcie realizacji, których rezerwacje obejmuje zmiana zgłoszona przez dostawcę.

    Zwraca pary (id delegacji, id zarezerwowanej usługi): przejazd danego kursu w danym dniu albo nocleg w danym
    obiekcie obejmujący noc z danego dnia.
    """
    if usluga == "transport":
        wiersze = s.execute(
            select(Przejazd.id_delegacji, Przejazd.id_przejazdu)
            .join(Delegacja, Delegacja.id_delegacji == Przejazd.id_delegacji)
            .where(
                Przejazd.id_polaczenia == id_polaczenia,
                Przejazd.data_przejazdu == data,
                Przejazd.status == StatusRezerwacji.WYBRANA,
                Delegacja.status == Status.W_TRAKCIE,
            )
        )
    else:
        wiersze = s.execute(
            select(Zakwaterowanie.id_delegacji, Zakwaterowanie.id_zakwaterowania)
            .join(Delegacja, Delegacja.id_delegacji == Zakwaterowanie.id_delegacji)
            .where(
                Zakwaterowanie.id_obiektu == id_obiektu,
                Zakwaterowanie.data_od <= data,
                Zakwaterowanie.data_do > data,
                Zakwaterowanie.status == StatusRezerwacji.WYBRANA,
                Delegacja.status == Status.W_TRAKCIE,
            )
        )
    return sorted((str(a), int(b)) for a, b in wiersze)


def _kwota(x: Decimal) -> str:
    return f"{x:.2f} zł"


def _opis_przejazdu(r: Przejazd, p: Polaczenie | None) -> str:
    kurs = f"{p.przewoznik} {p.skad} → {p.dokad}, odjazd {p.godzina_odjazdu:%H:%M}" if p else r.srodek_transportu
    return f"przejazd {r.kierunek.lower()} {r.data_przejazdu:%d.%m.%Y}: {kurs}"


def _opis_noclegu(z: Zakwaterowanie, o: ObiektZakwaterowania | None) -> str:
    nazwa = o.nazwa if o else z.id_obiektu
    adres = f", {o.miejscowosc}" if o else ""
    return f"nocleg {nazwa}{adres}, {z.data_od:%d.%m}–{z.data_do:%d.%m.%Y}"


def opis_uslugi(s: Session, z: ZmianaUslugi, zastepcza: bool = False) -> str:
    """Usługa objęta zmianą albo rozwiązanie zastępcze – opis do powiadomień, ekranu i dokumentacji."""
    if z.usluga == "transport":
        r = s.get(Przejazd, z.id_przejazdu_zastepczego if zastepcza else z.id_przejazdu)
        if r is None:
            return "–"
        return _opis_przejazdu(r, s.get(Polaczenie, r.id_polaczenia) if r.id_polaczenia else None)
    n = s.get(Zakwaterowanie, z.id_zakwaterowania_zastepczego if zastepcza else z.id_zakwaterowania)
    if n is None:
        return "–"
    return _opis_noclegu(n, s.get(ObiektZakwaterowania, n.id_obiektu))


def _godzina(chwila: datetime) -> str:
    """Czas w strefie użytkowników – w bazie przechowujemy UTC."""
    return chwila.replace(tzinfo=ZoneInfo("UTC")).astimezone(ZoneInfo("Europe/Warsaw")).strftime("%d.%m.%Y %H:%M")


# ---------------------------------------------------------------- dobór rozwiązania zastępczego (podzadanie 148)


def zastepczy_przejazd(
    s: Session, d: Delegacja, r: Przejazd, opoznienie_min: int | None = None
) -> tuple[Polaczenie, Decimal] | None:
    """Najlepszy przejazd na tej samej trasie i w tym samym dniu, osiągalny teraz i mieszczący się w budżecie.

    Przy opóźnieniu zamiennik musi dojechać wcześniej niż opóźniony kurs – inaczej zmiana nic nie daje.
    """
    skad, dokad = _trasa(s, d, r)
    stary = s.get(Polaczenie, r.id_polaczenia) if r.id_polaczenia else None
    najwczesniej = teraz() + ZAPAS_NA_DOJSCIE
    deadline = termin(s, d) if r.kierunek == "Tam" else None
    granica_przyjazdu = (
        przyjazd(stary, r.data_przejazdu) + timedelta(minutes=opoznienie_min) if stary and opoznienie_min else None
    )
    budzet = dostepny_budzet(s, d)
    oferty = []
    for p in s.scalars(select(Polaczenie)):
        if p.id_polaczenia == r.id_polaczenia or p.id_polaczenia.startswith("M-") or not _poprawne(p):
            continue
        if nazwa_miejscowosci(p.skad) != nazwa_miejscowosci(skad) or nazwa_miejscowosci(p.dokad) != nazwa_miejscowosci(
            dokad
        ):
            continue
        if datetime.combine(r.data_przejazdu, p.godzina_odjazdu) < najwczesniej:
            continue
        if deadline is not None and przyjazd(p, r.data_przejazdu) > deadline:
            continue
        if granica_przyjazdu is not None and przyjazd(p, r.data_przejazdu) >= granica_przyjazdu:
            continue
        zmiana = p.koszt - (r.koszt or Decimal("0"))
        if budzet is not None and zmiana > budzet:
            continue
        oferty.append((p, zmiana))
    oferty.sort(key=lambda o: ocena(o[0], r.data_przejazdu, deadline))
    return oferty[0] if oferty else None


def zastepczy_nocleg(
    s: Session, d: Delegacja, z: Zakwaterowanie, od: date
) -> tuple[ObiektZakwaterowania, Decimal, Decimal] | None:
    """Obiekt w miejscowości zadania z wolnym miejscem, dojazdem na czas i ceną w limicie – wojskowe pierwsze.

    Zwraca obiekt, koszt pobytu od `od` do końca rezerwacji i zmianę kosztu względem dotychczasowego obiektu.
    """
    p = s.get(Przedsiewziecie, d.id_przedsiewziecia) if d.id_przedsiewziecia else None
    stary = s.get(ObiektZakwaterowania, z.id_obiektu)
    miejscowosc = p.miejscowosc if p else (stary.miejscowosc if stary else d.miejsce_docelowe)
    maks_min = maks_czas_dojazdu(p.godzina_rozpoczecia if p else None)
    noce = max(1, (z.data_do - od).days)
    budzet = dostepny_budzet(s, d)
    obiekty = s.scalars(
        select(ObiektZakwaterowania).where(
            ObiektZakwaterowania.miejscowosc == miejscowosc,
            ObiektZakwaterowania.id_obiektu != z.id_obiektu,
            ObiektZakwaterowania.liczba_wolnych_miejsc > 0,
            ObiektZakwaterowania.cena_za_dobe <= LIMIT_NOCLEGOWY,
        )
    ).all()
    for o in sorted(obiekty, key=kolejnosc):
        if not terminowy_dojazd(o, maks_min):
            continue
        koszt = o.cena_za_dobe * noce
        zmiana = koszt - z.cena_za_dobe * noce
        if budzet is not None and zmiana > budzet:
            continue
        return o, koszt, zmiana
    return None


def _przekaz_dyzurnemu(s: Session, d: Delegacja, z: ZmianaUslugi, ktx: Kontekst, powod: str) -> None:
    """4a: sprawę przejmuje osoba dyżurna jednostki, delegowany dostaje informację."""
    z.status = StatusZmianyUslugi.U_DYZURNEGO
    z.id_dyzurnego = osoba_dyzurna(s, d)
    zastepcza = (
        s.get(Przejazd, z.id_przejazdu_zastepczego)
        if z.id_przejazdu_zastepczego
        else s.get(Zakwaterowanie, z.id_zakwaterowania_zastepczego)
        if z.id_zakwaterowania_zastepczego
        else None
    )
    if zastepcza is not None and zastepcza.status == StatusRezerwacji.ZASTEPCZA:
        zastepcza.status = StatusRezerwacji.ANULOWANA
    usluga = opis_uslugi(s, z)
    if z.id_dyzurnego:
        ktx.wyslij(
            Typ.POWIADOMIENIE,
            {
                "do": z.id_dyzurnego,
                "tresc": f"Delegacja {d.id_delegacji} w trakcie realizacji: {z.rodzaj_zdarzenia} – {usluga}. "
                f"{powod}. Sprawę przejmuje osoba dyżurna – skontaktuj się z delegowanym.",
            },
        )
    ktx.wyslij(
        Typ.POWIADOMIENIE,
        {
            "do": d.id_zolnierza,
            "tresc": f"Delegacja {d.id_delegacji}: {powod}. Sprawę ({usluga}) przekazano osobie dyżurnej jednostki"
            + (f" ({z.id_dyzurnego})" if z.id_dyzurnego else "")
            + ".",
        },
    )
    ktx.ostrzezenie(f"{powod} – sprawa u osoby dyżurnej {z.id_dyzurnego or '(brak w ewidencji)'}")


# ---------------------------------------------------------------- 1. zgłoszenie zmiany przez dostawcę (147–149)


def przygotuj_rozwiazanie(s: Session, k: Komunikat, ktx: Kontekst) -> None:
    d = pobierz_delegacje(s, k.id_delegacji)
    usluga, rodzaj = k.dane.get("usluga"), k.dane.get("rodzaj")
    if usluga not in USLUGI or rodzaj not in RODZAJE_ZDARZEN:
        raise BladProcesu("Nieznana usługa albo rodzaj zdarzenia zgłoszony przez dostawcę")
    if d.status != Status.W_TRAKCIE:
        ktx.ostrzezenie("Delegacja nie jest w trakcie realizacji – zmianę obsługuje się przed podróżą (-17, -41)")
        return
    id_uslugi = k.dane.get("id_uslugi")
    if usluga == "transport":
        rezerwacja = s.get(Przejazd, id_uslugi)
        pole = ZmianaUslugi.id_przejazdu
    else:
        rezerwacja = s.get(Zakwaterowanie, id_uslugi)
        pole = ZmianaUslugi.id_zakwaterowania
    if rezerwacja is None or rezerwacja.id_delegacji != d.id_delegacji:
        raise BladProcesu("Zgłoszona usługa nie należy do tej delegacji")
    if s.scalar(
        select(ZmianaUslugi.id_zmiany).where(pole == id_uslugi, ZmianaUslugi.status == StatusZmianyUslugi.OCZEKUJE)
    ):
        ktx.opis = "Zmiana tej usługi czeka już na odpowiedź delegowanego – pominięto ponowienie"
        return
    if rezerwacja.status != StatusRezerwacji.WYBRANA:
        ktx.opis = f"Usługa ma status „{rezerwacja.status}” – zmiana nie wymaga obsługi"
        return

    chwila = teraz()
    data_zdarzenia = date.fromisoformat(k.dane["data"]) if k.dane.get("data") else chwila.date()
    opis = str(k.dane.get("opis") or "").strip() or None
    opoznienie = int(k.dane.get("opoznienie_min") or 0) or None
    zmiana = ZmianaUslugi(
        id_delegacji=d.id_delegacji,
        usluga=usluga,
        rodzaj_zdarzenia=rodzaj,
        id_przejazdu=id_uslugi if usluga == "transport" else None,
        id_zakwaterowania=id_uslugi if usluga == "zakwaterowanie" else None,
        data_zdarzenia=data_zdarzenia,
        opis=(f"opóźnienie {opoznienie} min" + (f"; {opis}" if opis else "")) if opoznienie else opis,
        data_zgloszenia=chwila,
        status=StatusZmianyUslugi.OCZEKUJE,
    )
    s.add(zmiana)
    s.flush()
    zdarzenie = f"{rodzaj} – {opis_uslugi(s, zmiana)}" + (f" ({zmiana.opis})" if zmiana.opis else "")

    # Przy opóźnieniu usługa nadal działa – zamiennik tylko, gdy realnie poprawia sytuację
    if rodzaj == "opóźnienie" and (
        usluga == "zakwaterowanie" or zastepczy_przejazd(s, d, rezerwacja, opoznienie) is None
    ):
        zmiana.status = StatusZmianyUslugi.BEZ_ZMIAN
        ktx.opis = f"Zgłoszono {zdarzenie}; brak korzystniejszego rozwiązania – plan bez zmian"
        ktx.decyzja(Decyzja(typ=TYP_ROZWIAZANIA, wybrany_wariant="Bez zmian", uzasadnienie=zdarzenie))
        ktx.wyslij(
            Typ.POWIADOMIENIE,
            {
                "do": d.id_zolnierza,
                "tresc": f"Delegacja {d.id_delegacji}: {zdarzenie}. Rezerwacja pozostaje bez zmian.",
            },
        )
        return

    rezerwacja.status = StatusRezerwacji.ZAKLOCONA
    if usluga == "transport":
        wynik = zastepczy_przejazd(s, d, rezerwacja, opoznienie)
        if wynik is not None:
            p, roznica = wynik
            zastepcza = Przejazd(
                id_delegacji=d.id_delegacji,
                kierunek=rezerwacja.kierunek,
                srodek_transportu=p.srodek_transportu,
                data_przejazdu=rezerwacja.data_przejazdu,
                id_polaczenia=p.id_polaczenia,
                koszt=p.koszt,
                koszt_indywidualny=p.koszt,
                status=StatusRezerwacji.ZASTEPCZA,
                rekomendowany=True,
                uzasadnienie=f"Rozwiązanie zastępcze: przyjazd {przyjazd(p, rezerwacja.data_przejazdu):%H:%M}, "
                f"zmiana kosztu {roznica:+.2f} zł",
                powod_zmiany=f"Zamiennik przejazdu #{rezerwacja.id_przejazdu} ({rodzaj})",
            )
            s.add(zastepcza)
            s.flush()
            zmiana.id_przejazdu_zastepczego = zastepcza.id_przejazdu
            zmiana.koszt_zastepczy = p.koszt
            zmiana.zgodny_z_limitem = True  # koszt zmiany mieści się w budżecie jednostki
    else:
        od = max(data_zdarzenia, rezerwacja.data_od)
        wynik = zastepczy_nocleg(s, d, rezerwacja, od) if od < rezerwacja.data_do else None
        if wynik is not None:
            obiekt, koszt, roznica = wynik
            zastepcza = Zakwaterowanie(
                id_delegacji=d.id_delegacji,
                id_obiektu=obiekt.id_obiektu,
                data_od=od,
                data_do=rezerwacja.data_do,
                cena_za_dobe=obiekt.cena_za_dobe,
                koszt_calkowity=koszt,
                czas_dojazdu_min=obiekt.czas_dojazdu_min,
                wymaga_odstepstwa=False,
                status=StatusRezerwacji.ZASTEPCZA,
                uzasadnienie=f"Rozwiązanie zastępcze za {opis_uslugi(s, zmiana)}: {obiekt.rodzaj.lower()}, "
                f"{_kwota(obiekt.cena_za_dobe)} za dobę w limicie {_kwota(LIMIT_NOCLEGOWY)}, "
                f"zmiana kosztu {roznica:+.2f} zł",
            )
            s.add(zastepcza)
            s.flush()
            zmiana.id_zakwaterowania_zastepczego = zastepcza.id_zakwaterowania
            zmiana.koszt_zastepczy = koszt
            zmiana.zgodny_z_limitem = obiekt.cena_za_dobe <= LIMIT_NOCLEGOWY

    if wynik is None:
        ktx.decyzja(
            Decyzja(
                typ=TYP_ROZWIAZANIA, wybrany_wariant="Brak rozwiązania", uzasadnienie=zdarzenie, decyzja_finansowa=True
            )
        )
        _przekaz_dyzurnemu(s, d, zmiana, ktx, f"{zdarzenie}; brak rozwiązania zastępczego w limicie i budżecie")
        return

    zmiana.termin_odpowiedzi = chwila + CZAS_NA_ODPOWIEDZ
    rozwiazanie = opis_uslugi(s, zmiana, zastepcza=True)
    limit = "tak" if zmiana.zgodny_z_limitem else "nie"
    ktx.opis = f"Zgłoszono {zdarzenie}; rozwiązanie zastępcze: {rozwiazanie}"
    ktx.decyzja(
        Decyzja(
            typ=TYP_ROZWIAZANIA,
            wybrany_wariant=rozwiazanie,
            liczba_wariantow=1,
            uzasadnienie=f"{zdarzenie}; koszt {_kwota(zmiana.koszt_zastepczy)}, zgodność z limitem: {limit}",
            decyzja_finansowa=True,
        )
    )
    # Podzadanie 149: <numer delegacji> <zdarzenie> <rozwiązanie zastępcze> <termin odpowiedzi>
    ktx.wyslij(
        Typ.POWIADOMIENIE,
        {
            "do": d.id_zolnierza,
            "tresc": f"Delegacja {d.id_delegacji}: {zdarzenie}. Rozwiązanie zastępcze: {rozwiazanie}, koszt "
            f"{_kwota(zmiana.koszt_zastepczy)}, zgodność z limitem: {limit}. Odpowiedz do "
            f"{_godzina(zmiana.termin_odpowiedzi)} – po tym czasie sprawę przejmie osoba dyżurna jednostki.",
        },
    )


# ---------------------------------------------------------------- 2. odpowiedź delegowanego (150)


def _dopisz_do_polecenia(s: Session, d: Delegacja, wpis: dict) -> bool:
    polecenie = s.scalar(select(PolecenieWyjazdu).where(PolecenieWyjazdu.id_delegacji == d.id_delegacji))
    if polecenie is None:
        return False
    tresc = dict(polecenie.tresc or {})  # nowy słownik – zmiana kolumny JSON musi być widoczna dla sesji
    tresc["zmiany_w_podrozy"] = [*tresc.get("zmiany_w_podrozy", []), wpis]
    polecenie.tresc = tresc
    return True


def zapisz_odpowiedz(s: Session, k: Komunikat, ktx: Kontekst) -> None:
    d = pobierz_delegacje(s, k.id_delegacji)
    if k.dane.get("id_osoby") != d.id_zolnierza:
        raise BladProcesu("Na rozwiązanie zastępcze odpowiada delegowany")
    z = s.get(ZmianaUslugi, k.dane.get("id_zmiany"))
    if z is None or z.id_delegacji != d.id_delegacji:
        raise BladProcesu("Nie znaleziono zmiany w podróży tej delegacji")
    if z.status != StatusZmianyUslugi.OCZEKUJE:
        ktx.opis = f"Zmiana ma status „{z.status}” – odpowiedź nie jest już potrzebna"
        return
    chwila = teraz()
    z.data_odpowiedzi = chwila
    if not k.dane.get("akceptacja"):
        powod = str(k.dane.get("uzasadnienie") or "").strip()
        ktx.decyzja(Decyzja(typ=TYP_ODPOWIEDZI, wybrany_wariant="Odrzucenie", uzasadnienie=powod or "bez uzasadnienia"))
        _przekaz_dyzurnemu(s, d, z, ktx, "Delegowany odrzucił rozwiązanie zastępcze" + (f" ({powod})" if powod else ""))
        return

    rozwiazanie = opis_uslugi(s, z, zastepcza=True)
    if z.usluga == "transport":
        stara, nowa = s.get(Przejazd, z.id_przejazdu), s.get(Przejazd, z.id_przejazdu_zastepczego)
        roznica = (nowa.koszt or Decimal("0")) - (stara.koszt or Decimal("0"))
    else:
        stara, nowa = s.get(Zakwaterowanie, z.id_zakwaterowania), s.get(Zakwaterowanie, z.id_zakwaterowania_zastepczego)
        noce = max(1, (nowa.data_do - nowa.data_od).days)
        roznica = nowa.koszt_calkowity - stara.cena_za_dobe * noce
        obiekt = s.get(ObiektZakwaterowania, nowa.id_obiektu)
        if obiekt is not None and obiekt.liczba_wolnych_miejsc < 1:
            _przekaz_dyzurnemu(s, d, z, ktx, f"W obiekcie {obiekt.nazwa} zabrakło wolnych miejsc")
            return
    budzet = dostepny_budzet(s, d)
    if roznica > 0 and budzet is not None and roznica > budzet:
        _przekaz_dyzurnemu(s, d, z, ktx, "Rozwiązanie zastępcze przekracza już dostępny budżet jednostki")
        return
    if z.usluga == "zakwaterowanie":  # miejsce w nowym obiekcie zajęte, w dotychczasowym zwolnione
        if obiekt is not None:
            obiekt.liczba_wolnych_miejsc -= 1
        stary_obiekt = s.get(ObiektZakwaterowania, stara.id_obiektu)
        if stary_obiekt is not None:
            stary_obiekt.liczba_wolnych_miejsc += 1

    # Podzadanie 150: aktualizacja planu delegacji
    stara.status = StatusRezerwacji.ANULOWANA
    nowa.status = StatusRezerwacji.WYBRANA
    z.status = StatusZmianyUslugi.ZAAKCEPTOWANA
    zmiany = [f"nowa usługa: {rozwiazanie}"]
    jednostka = jednostka_delegacji(s, d)
    if jednostka is not None and roznica:
        jednostka.budzet_wykorzystany = max(Decimal("0"), (jednostka.budzet_wykorzystany or Decimal("0")) + roznica)
        zmiany.append(f"budżet jednostki {roznica:+.2f} zł")
    wpis = {
        "data": chwila.isoformat(timespec="minutes"),
        "zdarzenie": z.rodzaj_zdarzenia,
        "usluga": opis_uslugi(s, z),
        "rozwiazanie": rozwiazanie,
        "koszt": f"{z.koszt_zastepczy:.2f}" if z.koszt_zastepczy is not None else None,
        "zgodnosc_z_limitem": "tak" if z.zgodny_z_limitem else "nie",
    }
    if _dopisz_do_polecenia(s, d, wpis):
        zmiany.append("polecenie wyjazdu uzupełnione o zmianę")
    if z.usluga == "zakwaterowanie":
        from .zakwaterowanie_alternatywne import uzupelnij_dokumentacje  # sekcja „zakwaterowanie” (-15)

        uzupelnij_dokumentacje(s, d, nowa, s.get(ObiektZakwaterowania, nowa.id_obiektu))

    ktx.opis = "Zaakceptowano rozwiązanie zastępcze – " + "; ".join(zmiany)
    ktx.decyzja(
        Decyzja(
            typ=TYP_ODPOWIEDZI,
            wybrany_wariant="Akceptacja",
            uzasadnienie=f"Osoba: {d.id_zolnierza}; {ktx.opis}",
            decyzja_finansowa=roznica > 0,
        )
    )
    ktx.wyslij(
        Typ.POWIADOMIENIE,
        {"do": d.id_zolnierza, "tresc": f"Potwierdzenie zmiany w delegacji {d.id_delegacji}: " + "; ".join(zmiany)},
    )
    przelozony = przelozony_delegowanego(s, d)
    if przelozony:
        ktx.wyslij(
            Typ.POWIADOMIENIE,
            {
                "do": przelozony,
                "tresc": f"Delegacja {d.id_delegacji} (w trakcie): {z.rodzaj_zdarzenia} – {wpis['usluga']}. "
                f"Delegowany zaakceptował rozwiązanie zastępcze: {rozwiazanie}"
                + (f", zmiana kosztu {roznica:+.2f} zł" if roznica else "")
                + ".",
            },
        )


# ---------------------------------------------------------------- 3. brak odpowiedzi w terminie (151)


def zmiany_po_terminie(s: Session, chwila: datetime) -> list[str]:
    """Delegacje, w których rozwiązanie zastępcze czeka na odpowiedź dłużej niż wyznaczony termin."""
    return sorted(
        set(
            s.scalars(
                select(ZmianaUslugi.id_delegacji).where(
                    ZmianaUslugi.status == StatusZmianyUslugi.OCZEKUJE, ZmianaUslugi.termin_odpowiedzi <= chwila
                )
            )
        )
    )


def przekaz_po_terminie(s: Session, k: Komunikat, ktx: Kontekst) -> None:
    d = pobierz_delegacje(s, k.id_delegacji)
    chwila = teraz()
    po_terminie = [
        z
        for z in zmiany_uslug(s, d)
        if z.status == StatusZmianyUslugi.OCZEKUJE and z.termin_odpowiedzi and z.termin_odpowiedzi <= chwila
    ]
    if not po_terminie:
        ktx.opis = "Brak zmian czekających po terminie – kontrola nie jest potrzebna"
        return
    for z in po_terminie:
        ktx.decyzja(
            Decyzja(
                typ=TYP_ODPOWIEDZI,
                wybrany_wariant="Brak odpowiedzi",
                uzasadnienie=f"Termin {_godzina(z.termin_odpowiedzi)} minął bez odpowiedzi delegowanego",
            )
        )
        _przekaz_dyzurnemu(s, d, z, ktx, "Brak odpowiedzi na rozwiązanie zastępcze w wyznaczonym czasie")
