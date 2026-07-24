"""Empty query/form values must never 422 — they mean 'no selection' (regression).

Reported in use: /ledger?team_id= returned a raw 422 'unable to parse string as
an integer'. HTML 'All' options and unfilled selects submit empty strings, so any
int-typed query/form field must tolerate them.
"""
from __future__ import annotations

from sqlalchemy import select

from app import database
from app.models import Team
from tests.conftest import make_tournament


def test_ledger_empty_filters_ok(client):
    make_tournament(client)
    r = client.get("/ledger?team_id=&player_id=&status=&customer_name=&operator=&date_from=&date_to=")
    assert r.status_code == 200


def test_cashier_empty_params_ok(client):
    make_tournament(client)
    r = client.get("/cashier?customer_id=&team_id=&player_id=&qty=")
    assert r.status_code == 200


def test_results_empty_query_ok(client):
    make_tournament(client)
    assert client.get("/results?team_id=").status_code == 200


def test_placements_empty_selection_is_friendly(client):
    make_tournament(client)
    with database.SessionLocal() as s:
        team_id = s.scalars(select(Team).order_by(Team.id)).first().id
    r = client.post(f"/results/{team_id}/placements",
                    data={"first_player_id": "", "second_player_id": "", "third_player_id": ""},
                    follow_redirects=True)
    assert r.status_code == 200  # not 422
    assert "choose a player" in r.text.lower()


def test_cart_add_empty_fields_is_friendly(client):
    make_tournament(client)
    # Adding to the cart with empty fields (and no customer) must not 422.
    r = client.post("/cashier/cart/add", data={"team_id": "", "player_id": "", "quantity": ""},
                    follow_redirects=True)
    assert r.status_code == 200  # not 422
    assert "choose a customer" in r.text.lower()


def test_checkout_empty_order_is_friendly(client):
    make_tournament(client)
    r = client.post("/cashier/checkout", data={"received": ""}, follow_redirects=True)
    assert r.status_code == 200
    assert "order is empty" in r.text.lower()
