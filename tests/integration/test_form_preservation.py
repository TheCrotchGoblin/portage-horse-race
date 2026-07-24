"""On a validation error, forms must keep what was typed (re-render, not redirect)."""
from __future__ import annotations


def test_new_tournament_name_is_prefilled(client):
    # The name is a real, editable value (accept-as-is works), not a blank placeholder.
    r = client.get("/setup/new")
    assert 'value="Portage Men' in r.text
    assert "placeholder=\"Portage Men" not in r.text
    # Submitting the pre-filled default creates the tournament.
    r2 = client.post("/setup/new", data={
        "name": "Portage Men's Open 2026", "entry_price": "5.00", "club_percent": "15",
        "first_percent": "60", "second_percent": "30", "third_percent": "10",
    }, follow_redirects=True)
    assert "created" in r2.text.lower() or "Portage Men" in r2.text


def test_new_tournament_keeps_values_on_error(client):
    # Prize split doesn't total 100% -> should re-render with values preserved.
    r = client.post("/setup/new", data={
        "name": "My Big Open", "event_date": "2026-08-01", "entry_price": "5.00",
        "club_percent": "15", "first_percent": "50", "second_percent": "30", "third_percent": "10",
    }, follow_redirects=False)
    assert r.status_code == 400  # re-rendered, not a 303 redirect
    body = r.text
    assert "add up to exactly 100%" in body       # the error is shown
    assert 'value="My Big Open"' in body           # name preserved
    assert 'value="2026-08-01"' in body            # date preserved
    assert "is-invalid" in body                    # offending field highlighted


def test_settings_edit_keeps_values_on_error(client):
    client.post("/setup/new", data={
        "name": "Cfg Open", "entry_price": "5.00", "club_percent": "15",
        "first_percent": "60", "second_percent": "30", "third_percent": "10"})
    r = client.post("/setup/config", data={
        "name": "Renamed Open", "event_date": "", "entry_price": "7.00",
        "club_percent": "20", "first_percent": "70", "second_percent": "20", "third_percent": "20",
    }, follow_redirects=False)
    assert r.status_code == 400  # re-rendered
    assert "add up to exactly 100%" in r.text
    assert 'value="Renamed Open"' in r.text   # name kept
    assert 'value="7.00"' in r.text           # price kept


def test_customer_form_keeps_contact_on_error(client):
    # Blank name is invalid, but the phone/email typed should not be lost.
    r = client.post("/customers/new", data={
        "name": "   ", "phone": "204-555-9000", "email": "a@b.com",
    }, follow_redirects=False)
    assert r.status_code == 400
    assert 'value="204-555-9000"' in r.text
    assert 'value="a@b.com"' in r.text
    assert "name is required" in r.text.lower()
