from .base import Agent
from .koordynator import Koordynator
from .w_budowie import (
    AgentMonitorujacy,
    AgentRozliczen,
    AgentTransportu,
    AgentZakwaterowania,
    AgentZgodnosci,
)
from .wnioski import AgentWnioskow
from .zatwierdzenia import AgentZatwierdzen

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
