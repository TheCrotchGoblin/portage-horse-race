"""Navigation grouping / availability and the customer-create return flow."""
from __future__ import annotations

from tests.conftest import make_tournament


def test_event_tabs_disabled_without_tournament(client):
    home = client.get("/").text
    # Event tabs are shown but disabled until a tournament exists...
    assert "nav-link disabled" in home
    # ...while Setup, Customers and Admin stay available.
    assert 'href="/setup"' in home
    assert 'href="/customers"' in home
    assert 'href="/admin"' in home
    # Disabled Cashier is not a live link.
    assert 'href="/cashier"' not in home


def test_event_tabs_enabled_with_tournament(client):
    make_tournament(client)
    home = client.get("/").text
    assert 'href="/cashier"' in home
    assert 'href="/reports"' in home
    assert "nav-divider" in home  # grouped with separators


def test_new_customer_returns_to_list(client):
    r = client.post("/customers/new", data={"name": "Jo Lister"}, follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/customers"
    # And the new customer shows up on the list.
    assert "Jo Lister" in client.get("/customers").text


def test_new_customer_from_cashier_still_selects(client):
    make_tournament(client)
    r = client.post("/customers/new", data={"name": "Cash Cust", "return_to": "cashier"},
                    follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"].startswith("/cashier?customer_id=")
