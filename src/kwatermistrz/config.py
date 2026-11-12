"""Konfiguracja z zmiennych środowiskowych (lokalnie: plik .env, w Azure: ustawienia Container Apps)."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://kwatermistrz:kwatermistrz@localhost:5432/kwatermistrz"
    amqp_url: str = "amqp://guest:guest@localhost:5672/"
    exchange: str = "kwatermistrz"
    polacz_broker: bool = True

    # S22IT1S0P2-13: termin na decyzję przełożonego i dodatkowy termin po przypomnieniu
    termin_decyzji_godz: float = 48
    termin_po_przypomnieniu_godz: float = 24
    interwal_kontroli_terminow_s: float = 60

    # Liczba prób obsługi komunikatu, zanim operacja zostanie uznana za nieudaną
    max_prob: int = 3


settings = Settings()
