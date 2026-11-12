"""Zapis logu operacji i logu decyzji agentów."""

from dataclasses import dataclass

from sqlalchemy.orm import Session

from .models import LogDecyzji, LogOperacji

# Decyzja probabilistyczna poniżej tej pewności trafia do weryfikacji człowieka
PROG_PEWNOSCI = 0.75


@dataclass
class Decyzja:
    typ: str
    wybrany_wariant: str
    uzasadnienie: str
    rodzaj: str = "Regułowa"  # albo "Probabilistyczna"
    liczba_wariantow: int = 1
    pewnosc: float = 1.0
    decyzja_finansowa: bool = False  # np. koszt ponad limit – zawsze do weryfikacji

    @property
    def wymaga_zatwierdzenia(self) -> bool:
        return self.decyzja_finansowa or (self.rodzaj == "Probabilistyczna" and self.pewnosc < PROG_PEWNOSCI)


def zapisz_operacje(s: Session, **pola) -> LogOperacji:
    op = LogOperacji(**pola)
    s.add(op)
    s.flush()  # nadaje id_operacji, potrzebne do powiązania decyzji
    return op


def zapisz_decyzje(s: Session, operacja: LogOperacji, d: Decyzja, wersja_modelu: str) -> LogDecyzji:
    wpis = LogDecyzji(
        id_operacji=operacja.id_operacji,
        typ_decyzji=d.typ,
        rodzaj_decyzji=d.rodzaj,
        liczba_wariantow=d.liczba_wariantow,
        wybrany_wariant=d.wybrany_wariant,
        uzasadnienie=d.uzasadnienie,
        pewnosc=d.pewnosc,
        wersja_modelu=wersja_modelu,
        wymaga_zatwierdzenia=d.wymaga_zatwierdzenia,
    )
    s.add(wpis)
    return wpis
