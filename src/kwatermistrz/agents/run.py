"""Uruchomienie agentów: `kwatermistrz-agenci <nazwa>[,<nazwa>…]` albo `all` (domyślnie).

W Azure wszyscy agenci działają w jednym kontenerze (`all`), żeby zmieścić się w budżecie Azure for Students –
logicznie pozostają niezależni: każdy ma własną kolejkę, własną obsługę błędów i własny wpis w logu.
"""

import asyncio
import logging
import os
import sys

from ..config import settings
from ..messaging import Broker
from . import AGENCI

log = logging.getLogger("kwatermistrz.agents")


async def uruchom(nazwy: list[str]) -> None:
    broker = Broker(settings.amqp_url, settings.exchange)
    await broker.polacz()
    zadania = []
    for nazwa in nazwy:
        agent = AGENCI[nazwa](broker)
        await broker.subskrybuj(agent.kolejka, tuple(agent.obslugi()), agent.na_komunikat)
        if agent.interwal_cyklu:
            zadania.append(asyncio.create_task(agent.petla_cykliczna()))
        log.info("Uruchomiono agenta %s (%s), kolejka %s", agent.id_agenta, nazwa, agent.kolejka)
    try:
        await asyncio.Event().wait()
    finally:
        await broker.zamknij()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    arg = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("AGENT", "all")
    nazwy = list(AGENCI) if arg == "all" else arg.split(",")
    nieznane = [n for n in nazwy if n not in AGENCI]
    if nieznane:
        sys.exit(f"Nieznani agenci: {', '.join(nieznane)}. Dostępni: {', '.join(AGENCI)}")
    asyncio.run(uruchom(nazwy))


if __name__ == "__main__":
    main()
