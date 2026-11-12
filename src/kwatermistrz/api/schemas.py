from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class WniosekIn(BaseModel):
    """Wniosek o wyjazd. Pola nie są tu wymagane – kompletność i spójność sprawdza Agent Wniosków."""

    id_zolnierza: str
    id_przedsiewziecia: str | None = None
    cel_wyjazdu: str | None = None
    miejsce_docelowe: str | None = None
    data_wyjazdu: str | None = Field(None, examples=["2026-12-07"])
    data_powrotu: str | None = Field(None, examples=["2026-12-11"])
    srodek_transportu: str | None = Field(None, examples=["pociąg"])
    dane_transportu: dict[str, str] = Field(default_factory=dict)


class DecyzjaIn(BaseModel):
    id_osoby: str
    decyzja: Literal["Zgoda", "Odmowa", "Do uzupełnienia"]
    uzasadnienie: str | None = None


class DelegacjaOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id_delegacji: str
    id_zolnierza: str
    status: str
    data_zmiany_statusu: datetime | None
    data_zlozenia: datetime | None
    cel_wyjazdu: str | None
    miejsce_docelowe: str | None
    data_wyjazdu: date | None
    data_powrotu: date | None
    id_zatwierdzajacego: str | None
    termin_decyzji: datetime | None
    liczba_przypomnien: int
    przekazano_zastepcy: bool
    decyzja: str | None


class DecyzjaAgentaOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    typ_decyzji: str
    rodzaj_decyzji: str
    liczba_wariantow: int
    wybrany_wariant: str
    uzasadnienie: str
    pewnosc: float
    wersja_modelu: str
    wymaga_zatwierdzenia: bool
    decyzja_czlowieka: str | None


class OperacjaOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id_operacji: int
    znacznik_czasu: datetime
    id_agenta: str
    etap: str
    operacja: str
    wynik: str
    czas_trwania_ms: int
    nr_proby: int
    komunikat: str | None
    decyzje: list[DecyzjaAgentaOut] = []


class ZolnierzOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id_zolnierza: str
    stopien: str
    imie_nazwisko: str
    id_jednostki: str
    funkcja: str | None
    id_przelozonego: str | None
    id_zastepcy: str | None
