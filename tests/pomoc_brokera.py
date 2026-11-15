"""Automatyczne dostarczanie komunikatów wyłącznie w testach integracyjnych.

Korzysta z istniejącej atrapy testowej. Nie jest brokerem ani narzędziem do
uruchamiania aplikacji; nie należy do pakietu kwatermistrz.
"""

from conftest import AtrapaBrokera

from kwatermistrz.agents import AGENCI


class BrokerTestowy(AtrapaBrokera):
    def __init__(self):
        super().__init__()
        self.agenci = [klasa(self) for klasa in AGENCI.values()]
        self._dostarczanie = False

    async def publikuj(self, komunikat):
        await super().publikuj(komunikat)
        if self._dostarczanie:
            return
        self._dostarczanie = True
        try:
            await self.dostarczaj(self.agenci)
        finally:
            self._dostarczanie = False
