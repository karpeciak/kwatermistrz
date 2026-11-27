"""Komunikacja agentów przez RabbitMQ (exchange typu topic, jedna trwała kolejka na agenta)."""

import json
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime

import aio_pika
from aio_pika.abc import AbstractIncomingMessage


class Typ:
    """Klucze routingu komunikatów.

    Konwencja: `zadanie.*` – Koordynator zleca pracę agentowi, `wynik.*` – agent odsyła wynik Koordynatorowi,
    `decyzja.*`, `wybor.*`, `akceptacja.*` – działanie człowieka przyjęte przez API.
    """

    # Wniosek (S22IT1S0P2-33)
    WNIOSEK_ZLOZONY = "wniosek.zlozony"
    ZADANIE_WALIDACJI = "zadanie.walidacja"
    WYNIK_WALIDACJI = "wynik.walidacja"
    # Zatwierdzenie (S22IT1S0P2-13, -14, -35)
    ZADANIE_ZATWIERDZENIA = "zadanie.zatwierdzenie"
    DECYZJA_PRZELOZONEGO = "decyzja.przelozonego"
    WYNIK_ZATWIERDZENIA = "wynik.zatwierdzenie"
    # Organizacja wyjazdu (S22IT1S0P2-38, -27, -39)
    ZADANIE_POLECENIA = "zadanie.polecenie"
    WYNIK_POLECENIA = "wynik.polecenie"
    ZADANIE_TRANSPORTU = "zadanie.transport"
    WYBOR_TRANSPORTU = "wybor.transport"
    PROPOZYCJA_TRANSPORTU = "decyzja.propozycja_transportu"
    WYNIK_TRANSPORTU = "wynik.transport"
    ZADANIE_ZAKWATEROWANIA = "zadanie.zakwaterowanie"
    WYBOR_ZAKWATEROWANIA = "wybor.zakwaterowanie"
    WYNIK_ZAKWATEROWANIA = "wynik.zakwaterowanie"
    # Zakwaterowanie w limicie i odstępstwo od limitu (S22IT1S0P2-40)
    WYBOR_OBIEKTU = "wybor.obiekt_zakwaterowania"
    DECYZJA_ODSTEPSTWA = "decyzja.odstepstwo_noclegu"
    # Realizacja (sprint 3)
    ZADANIE_MONITOROWANIA = "zadanie.monitorowanie"
    # Zmiana przejazdu po odwołaniu przez przewoźnika (S22IT1S0P2-17)
    ODWOLANIE_PRZEJAZDU = "zdarzenie.odwolanie_przejazdu"
    WYBOR_PRZEJAZDU_ALTERNATYWNEGO = "wybor.przejazd_alternatywny"
    # Rozliczenie (S22IT1S0P2-36, -20, -43)
    ZADANIE_ROZLICZENIA = "zadanie.rozliczenie"
    WYNIK_ROZLICZENIA = "wynik.rozliczenie"
    ZADANIE_WERYFIKACJI = "zadanie.weryfikacja"
    WYNIK_WERYFIKACJI = "wynik.weryfikacja"
    AKCEPTACJA_ROZLICZENIA = "akceptacja.rozliczenia"
    WYNIK_PRZEKAZANIA = "wynik.przekazanie"

    POWIADOMIENIE = "powiadomienie"


@dataclass
class Komunikat:
    typ: str
    id_delegacji: str | None = None
    dane: dict = field(default_factory=dict)
    id_korelacji: str | None = None
    proba: int = 1
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    czas: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    def __post_init__(self) -> None:
        if self.id_korelacji is None and self.id_delegacji:
            self.id_korelacji = f"COR-{self.id_delegacji}"

    def do_json(self) -> bytes:
        return json.dumps(asdict(self), ensure_ascii=False, default=str).encode()

    @classmethod
    def z_json(cls, body: bytes) -> "Komunikat":
        return cls(**json.loads(body))


class Broker:
    def __init__(self, url: str, exchange: str) -> None:
        self.url = url
        self.nazwa_exchange = exchange
        self._polaczenie: aio_pika.abc.AbstractRobustConnection | None = None

    async def polacz(self) -> None:
        self._polaczenie = await aio_pika.connect_robust(self.url)
        self._kanal = await self._polaczenie.channel()
        await self._kanal.set_qos(prefetch_count=10)
        self._exchange = await self._kanal.declare_exchange(
            self.nazwa_exchange, aio_pika.ExchangeType.TOPIC, durable=True
        )

    @staticmethod
    def _wiadomosc(k: Komunikat) -> aio_pika.Message:
        return aio_pika.Message(
            k.do_json(),
            content_type="application/json",
            delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
            message_id=k.id,
            correlation_id=k.id_korelacji,
        )

    async def publikuj(self, k: Komunikat) -> None:
        await self._exchange.publish(self._wiadomosc(k), routing_key=k.typ)

    async def do_kolejki(self, kolejka: str, k: Komunikat) -> None:
        """Wysyła komunikat bezpośrednio do kolejki jednego agenta (ponowienie po błędzie)."""
        await self._kanal.default_exchange.publish(self._wiadomosc(k), routing_key=kolejka)

    async def subskrybuj(
        self,
        kolejka: str,
        klucze: tuple[str, ...],
        obsluga: Callable[[AbstractIncomingMessage], Awaitable[None]],
    ) -> None:
        q = await self._kanal.declare_queue(kolejka, durable=True)
        for klucz in klucze:
            await q.bind(self._exchange, routing_key=klucz)
        await q.consume(obsluga)

    async def zamknij(self) -> None:
        if self._polaczenie:
            await self._polaczenie.close()
