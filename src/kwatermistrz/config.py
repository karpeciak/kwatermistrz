"""Konfiguracja z zmiennych środowiskowych (lokalnie: plik .env, w Azure: ustawienia Container Apps)."""

from secrets import token_hex

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://kwatermistrz:kwatermistrz@localhost:5432/kwatermistrz"
    amqp_url: str = "amqp://guest:guest@localhost:5672/"
    exchange: str = "kwatermistrz"
    polacz_broker: bool = True
    # Publiczne API dla niewielkiego ruchu. Przy większym ruchu ustaw własną instancję Photon.
    photon_url: str = "https://photon.komoot.io/api/"
    # W Azure ustaw SESSION_SECRET, aby sesje przetrwały restart i działały między replikami.
    session_secret: SecretStr = Field(default_factory=lambda: SecretStr(token_hex(32)))

    # S22IT1S0P2-13: termin na decyzję przełożonego i dodatkowy termin po przypomnieniu
    termin_decyzji_godz: float = 48
    termin_po_przypomnieniu_godz: float = 24
    interwal_kontroli_terminow_s: float = 60

    # Liczba prób obsługi komunikatu, zanim operacja zostanie uznana za nieudaną
    max_prob: int = 3


settings = Settings()
