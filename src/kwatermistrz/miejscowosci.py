"""Wyszukiwanie polskich miejscowości w Photon (OpenStreetMap).

Wysyłamy wyłącznie nazwę z pola wyszukiwania. Bez danych wniosku i żołnierza.
Ograniczenie jednego zapytania/s oraz cache dotyczą pojedynczego procesu.
"""

import json
import logging
import socket
from collections import OrderedDict
from threading import Lock
from time import monotonic
from urllib.error import URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .config import settings


class BladMiejscowosci(ValueError):
    pass


class ZrodloNiedostepne(RuntimeError):
    pass


_blokada = Lock()
_cache = OrderedDict()
_ostatnie = float("-inf")
_log = logging.getLogger(__name__)


def szukaj_miejscowosci(tekst: str) -> list[dict]:
    global _ostatnie
    tekst = " ".join(tekst.split())
    if not 2 <= len(tekst) <= 80 or not all(c.isalpha() or c in " -'’" for c in tekst):
        raise BladMiejscowosci("Wpisz od 2 do 80 znaków nazwy miejscowości, bez adresu i numerów.")
    klucz = (settings.photon_url, tekst.casefold())
    with _blokada:
        teraz = monotonic()
        if klucz in _cache:
            czas, wynik = _cache[klucz]
            if teraz - czas < 86400:
                _cache.move_to_end(klucz)
                return [dict(w) for w in wynik]
        if teraz - _ostatnie < 1:
            raise ZrodloNiedostepne("Odczekaj chwilę przed kolejnym wyszukiwaniem.")
        _ostatnie = teraz
        parametry = urlencode(
            [
                ("q", tekst),
                ("countrycode", "PL"),
                ("limit", 8),
                ("lang", "pl"),
                ("layer", "city"),
                ("layer", "locality"),
            ]
        )
        req = Request(
            settings.photon_url.rstrip("?") + "?" + parametry,
            headers={
                "User-Agent": "Kwatermistrz-S22IT1S0P2/0.1 (+https://github.com/karpeciak/kwatermistrz)",
                "Accept": "application/json",
            },
        )
        try:
            with urlopen(req, timeout=5) as response:
                dane = json.loads(response.read(262145))
            if not isinstance(dane, dict) or not isinstance(dane.get("features"), list):
                raise ValueError("Nieprawidłowa odpowiedź Photon")
            wynik, widziane = [], set()
            for feature in dane["features"][:8]:
                p = feature.get("properties", {})
                nazwa = p.get("name")
                if p.get("countrycode", "").upper() != "PL" or not isinstance(nazwa, str) or not nazwa:
                    continue
                opis = ", ".join(str(p[k]) for k in ("state", "county") if p.get(k))
                if (nazwa, opis) not in widziane:
                    wynik.append({"nazwa": nazwa, "opis": opis})
                    widziane.add((nazwa, opis))
        except (URLError, OSError, ValueError, TypeError, AttributeError) as e:
            # Bez wyszukiwanego tekstu i danych wniosku; szczegóły tylko w logu serwera.
            _log.warning("Błąd źródła miejscowości (%s): %s", type(e).__name__, e)
            if isinstance(e, URLError) and isinstance(e.reason, socket.gaierror):
                raise ZrodloNiedostepne(
                    "Nie można ustalić adresu serwera wyszukiwarki miejscowości (błąd DNS). "
                    "Możesz wpisać nazwę samodzielnie."
                ) from e
            raise ZrodloNiedostepne(
                "Nie udało się pobrać wyników z API miejscowości. Możesz wpisać nazwę samodzielnie."
            ) from e
        _cache[klucz] = (monotonic(), wynik)
        while len(_cache) > 256:
            _cache.popitem(last=False)
        return [dict(w) for w in wynik]
