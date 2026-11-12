from .base import Agent
from .koordynator import Koordynator
from .monitorujacy import AgentMonitorujacy
from .rozliczenia import AgentRozliczen
from .transport import AgentTransportu
from .wnioski import AgentWnioskow
from .zakwaterowanie import AgentZakwaterowania
from .zatwierdzenia import AgentZatwierdzen
from .zgodnosc import AgentZgodnosci

AGENCI: dict[str, type[Agent]] = {
    a.nazwa: a
    for a in (
        Koordynator,
        AgentWnioskow,
        AgentZatwierdzen,
        AgentTransportu,
        AgentZakwaterowania,
        AgentRozliczen,
        AgentZgodnosci,
        AgentMonitorujacy,
    )
}
