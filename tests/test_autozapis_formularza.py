"""S22IT1S0P2-79: autozapis nie może wisieć na samym <form>.

HTMX waliduje formularz (pola `required`) przed wysłaniem żądania, gdy to formularz je wysyła – wtedy częściowo
wypełniony wniosek nie był zapisywany. Autozapis musi być na osobnym elemencie nasłuchującym zmian w formularzu.
"""

import re

import pytest
from fastapi.testclient import TestClient
from pomoc_sesji import zaloguj_z_csrf

from kwatermistrz.api.main import app, get_broker


@pytest.fixture
def klient(broker):
    app.dependency_overrides[get_broker] = lambda: broker
    with TestClient(app, follow_redirects=False) as c:
        zaloguj_z_csrf(c, "ZOL-003")
        yield c
    app.dependency_overrides.clear()


def test_autozapis_poza_formularzem_bez_walidacji(klient):
    strona = klient.get("/app/wnioski/nowy").text
    znacznik_formularza = re.search(r'<form[^>]*id="formularz-wniosku"[^>]*>', strona).group(0)
    assert "hx-post" not in znacznik_formularza

    autozapis = re.search(r'<span id="autozapis"[^>]*>', strona).group(0)
    assert 'hx-post="/app/wnioski/nowy/roboczy"' in autozapis
    assert 'hx-include="#formularz-wniosku"' in autozapis
    assert "from:#formularz-wniosku" in autozapis
