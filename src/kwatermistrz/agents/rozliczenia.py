"""Agent Rozliczeń – S22IT1S0P2-36 Wyliczenie należności z delegacji na podstawie dokumentów kosztowych.

Przebieg:
1. Delegowany przekazuje faktyczny czas podróży, zapewnione świadczenia i dokumenty kosztowe (`api/rozliczenia.py`,
   ekran `/app/wnioski/{id}/rozliczenie`) → `zadanie.rozliczenie` → `wylicz`.
2. `wylicz` (reguły w `wylicz_naleznosci`, stawki w `stawki.py`) – S22IT1S0P2-61, -63:
   - dieta: podróż do doby – poniżej 8 h nie przysługuje, 8–12 h połowa, ponad 12 h całość; dłuższa – każda pełna
     doba całość, niepełna do 8 h połowa, ponad 8 h całość; pomniejszona o zapewnione posiłki,
   - zwrot przejazdu: bilety oraz pozostałe dokumenty (faktura, inny),
   - zwrot noclegu: rachunki do wysokości `LIMIT_NOCLEGOWY` × liczba noclegów; nadwyżka → `przekracza_limit=True`,
     `kwota_uznana` = do limitu (scenariusz 1a–2a),
   - ryczałty: komunikacja miejscowa za każdą rozpoczętą dobę; nocleg bez rachunku i bez bezpłatnego
     zakwaterowania – ryczałt za każdy nocleg,
   - decyzja w logu; przekroczenie limitu to decyzja finansowa – trafia do sprawdzenia przez człowieka.
3. Delegowany zatwierdza wyliczenie (`api/rozliczenia.zatwierdz_wyliczenie`) → status „gotowe do weryfikacji”,
   budżet jednostki: zaangażowanie → wydatek (S22IT1S0P2-66), `wynik.rozliczenie`.

Duplikaty dokumentów (S22IT1S0P2-20) – osobny moduł `dokumenty_kosztowe.py`.
"""

import math
from dataclasses import dataclass, field
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..audit import Decyzja
from ..messaging import Komunikat, Typ
from ..models import DokumentKosztowy, Rozliczenie, StatusRozliczenia
from ..stawki import DIETA_KRAJOWA, LIMIT_NOCLEGOWY, POTRACENIE_ZA_POSILEK, RYCZALT_DOJAZDY, RYCZALT_NOCLEG
from .base import Agent, Kontekst, pobierz_delegacje

GROSZ = Decimal("0.01")
RACHUNEK_ZA_NOCLEG = "rachunek za nocleg"
TYP_DECYZJI = "Wyliczenie należności"


def zl(kwota: Decimal) -> Decimal:
    return kwota.quantize(GROSZ, rounding=ROUND_HALF_UP)


def czesc_diety(godziny: float) -> Decimal:
    """Ile pełnych diet przysługuje za podróż trwającą podaną liczbę godzin."""
    if godziny <= 24:
        if godziny < 8:
            return Decimal("0")
        return Decimal("0.5") if godziny <= 12 else Decimal("1")
    pelne_doby, reszta = divmod(godziny, 24)
    czesc = Decimal(int(pelne_doby))
    if reszta > 0:
        czesc += Decimal("0.5") if reszta <= 8 else Decimal("1")
    return czesc


@dataclass
class Wyliczenie:
    dieta: Decimal
    zwrot_przejazdu: Decimal
    zwrot_noclegu: Decimal
    ryczalty: Decimal
    kwoty_uznane: list[Decimal] = field(default_factory=list)

    @property
    def kwota_naleznosci(self) -> Decimal:
        return zl(self.dieta + self.zwrot_przejazdu + self.zwrot_noclegu + self.ryczalty)


def wylicz_naleznosci(
    wyjazd: datetime,
    powrot: datetime,
    posilki: dict[str, int],
    bezplatne_zakwaterowanie: bool,
    dokumenty: list[tuple[str, Decimal]],
) -> Wyliczenie:
    """Składniki należności z faktycznego czasu podróży, świadczeń i dokumentów (rodzaj, kwota) w kolejności dat."""
    godziny = (powrot - wyjazd).total_seconds() / 3600
    potracenie = sum(
        (
            DIETA_KRAJOWA * POTRACENIE_ZA_POSILEK[posilek] * liczba
            for posilek, liczba in posilki.items()
            if posilek in POTRACENIE_ZA_POSILEK
        ),
        Decimal("0"),
    )
    dieta = max(Decimal("0"), DIETA_KRAJOWA * czesc_diety(godziny) - potracenie)
    rozpoczete_doby = max(1, math.ceil(godziny / 24))
    noclegi = max(0, (powrot.date() - wyjazd.date()).days)

    limit_noclegow = LIMIT_NOCLEGOWY * noclegi
    przejazd = nocleg = Decimal("0")
    uznane: list[Decimal] = []
    rachunki = 0
    for rodzaj, kwota in dokumenty:
        if rodzaj.strip().casefold() == RACHUNEK_ZA_NOCLEG:
            rachunki += 1
            uznana = min(kwota, max(Decimal("0"), limit_noclegow - nocleg))
            nocleg += uznana
        else:
            uznana = kwota
            przejazd += uznana
        uznane.append(zl(uznana))

    ryczalty = RYCZALT_DOJAZDY * rozpoczete_doby
    if noclegi and not rachunki and not bezplatne_zakwaterowanie:
        ryczalty += RYCZALT_NOCLEG * noclegi
    return Wyliczenie(zl(dieta), zl(przejazd), zl(nocleg), zl(ryczalty), uznane)


class AgentRozliczen(Agent):
    id_agenta = "AG-06"
    nazwa = "rozliczenia"
    etap = "Rozliczenie"
    wersja_modelu = "naleznosci-0.1"

    def obslugi(self):
        return {Typ.ZADANIE_ROZLICZENIA: (self.wylicz, "Wyliczenie należności")}

    def wylicz(self, s: Session, k: Komunikat, ktx: Kontekst) -> None:
        d = pobierz_delegacje(s, k.id_delegacji)
        r = s.scalar(select(Rozliczenie).where(Rozliczenie.id_delegacji == d.id_delegacji))
        if r is None or r.faktyczny_wyjazd is None or r.faktyczny_powrot is None:
            ktx.ostrzezenie("Brak faktycznego czasu podróży – wyliczenie wstrzymane do jego uzupełnienia")
            return
        if r.status != StatusRozliczenia.W_PRZYGOTOWANIU:
            ktx.ostrzezenie(f"Rozliczenie ma status „{r.status}” – ponowne wyliczenie pominięte")
            return

        dokumenty = s.scalars(
            select(DokumentKosztowy)
            .where(DokumentKosztowy.id_rozliczenia == r.id_rozliczenia)
            .order_by(DokumentKosztowy.data, DokumentKosztowy.id_dokumentu)
        ).all()
        w = wylicz_naleznosci(
            r.faktyczny_wyjazd,
            r.faktyczny_powrot,
            r.zapewnione_posilki or {},
            r.bezplatne_zakwaterowanie,
            [(dok.rodzaj, dok.kwota) for dok in dokumenty],
        )

        # S22IT1S0P2-63: pozycja uwzględniona do wysokości limitu i oznaczona
        ponad_limit = []
        for dok, uznana in zip(dokumenty, w.kwoty_uznane, strict=True):
            dok.kwota_uznana = uznana
            dok.przekracza_limit = uznana < dok.kwota
            if dok.przekracza_limit:
                ponad_limit.append(f"{dok.rodzaj} {dok.numer}: {dok.kwota:.2f} zł, uznano {uznana:.2f} zł")

        r.dieta = w.dieta
        r.zwrot_przejazdu = w.zwrot_przejazdu
        r.zwrot_noclegu = w.zwrot_noclegu
        r.ryczalty = w.ryczalty
        r.kwota_naleznosci = w.kwota_naleznosci

        skladniki = (
            f"dieta {w.dieta:.2f} zł, przejazd {w.zwrot_przejazdu:.2f} zł, nocleg {w.zwrot_noclegu:.2f} zł, "
            f"ryczałty {w.ryczalty:.2f} zł"
        )
        ktx.opis = f"Należność {w.kwota_naleznosci:.2f} zł ({skladniki})"
        if ponad_limit:
            ktx.ostrzezenie("Przekroczenie limitu – " + "; ".join(ponad_limit))
        ktx.decyzja(
            Decyzja(
                typ=TYP_DECYZJI,
                wybrany_wariant=f"{w.kwota_naleznosci:.2f} zł",
                uzasadnienie=skladniki + (f"; ponad limit: {'; '.join(ponad_limit)}" if ponad_limit else ""),
                decyzja_finansowa=bool(ponad_limit),
            )
        )
