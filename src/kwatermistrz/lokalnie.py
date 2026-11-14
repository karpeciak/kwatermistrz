"""Dostarczanie wiadomości w jednym procesie do lokalnych testów całej aplikacji.

Wdrożenie korzysta z Broker/RabbitMQ. Ten adapter uruchamia te same klasy agentów,
ale nie odtwarza trwałości i niezależnych procesów produkcyjnego brokera.
"""

from collections import deque

from .agents import AGENCI
from .messaging import Komunikat


class BrokerLokalny:
    def __init__(self):
        self.agenci = [klasa(self) for klasa in AGENCI.values()]
        self.oczekujace: deque[tuple[str | None, Komunikat]] = deque()
        self.przetwarzanie = False

    async def publikuj(self, k: Komunikat):
        self.oczekujace.append((None, k))
        await self.dostarcz()

    async def do_kolejki(self, kolejka: str, k: Komunikat):
        self.oczekujace.append((kolejka, k))
        await self.dostarcz()

    async def dostarcz(self):
        if self.przetwarzanie:
            return
        self.przetwarzanie = True
        try:
            while self.oczekujace:
                kolejka, komunikat = self.oczekujace.popleft()
                for agent in self.agenci:
                    if kolejka is not None and agent.kolejka != kolejka:
                        continue
                    obsluga = agent.obslugi().get(komunikat.typ)
                    if obsluga:
                        await agent.wykonaj(komunikat, *obsluga)
        finally:
            self.przetwarzanie = False
