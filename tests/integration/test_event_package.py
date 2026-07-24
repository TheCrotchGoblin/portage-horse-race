"""Settlement package export (RPT-02): one ZIP with the permanent event record."""
from __future__ import annotations

import io
import zipfile

from sqlalchemy import select

from app import database
from app.models import Customer, Player, Team
from tests.conftest import make_tournament, place_wager


def _seed_settled(client):
    make_tournament(client)
    with database.SessionLocal() as s:
        team = s.scalars(select(Team).order_by(Team.id)).first()
        players = [p.id for p in s.scalars(select(Player).where(Player.team_id == team.id).order_by(Player.id)).all()]
        team_id = team.id
    client.post("/customers/new", data={"name": "Backer"})
    with database.SessionLocal() as s:
        cid = s.scalars(select(Customer)).first().id
    for pid in players:
        place_wager(client, cid, team_id, pid, 2)
    client.post(f"/results/{team_id}/placements",
                data={"first_player_id": players[0], "second_player_id": players[1], "third_player_id": players[2]})
    client.post("/setup/close")
    client.post(f"/results/{team_id}/generate")


def test_settlement_package_contains_full_record(client):
    _seed_settled(client)
    r = client.post("/reports/settlement-package")
    assert r.status_code == 200
    assert "zip" in r.headers["content-type"]

    z = zipfile.ZipFile(io.BytesIO(r.content))
    names = z.namelist()
    for expected in ("reconciliation.txt", "configuration.json", "customers.csv",
                     "wagers.csv", "player_totals.csv", "payouts.csv", "audit_log.csv"):
        assert expected in names, f"missing {expected}"
    assert any(n.startswith("database_backup/") for n in names), "missing DB backup"
    # Reconciliation text is non-trivial and mentions balancing.
    recon = z.read("reconciliation.txt").decode("utf-8")
    assert "RECONCILIATION" in recon and "ALL TEAMS" in recon


def test_reconciliation_accounts_for_reversed_payout(client):
    """A reversed payout must not vanish from the reconciliation (review Finding 1)."""
    from app.models import Payout, Team
    from app.services.event_package import _payout_totals

    _seed_settled(client)
    with database.SessionLocal() as s:
        pid = s.scalars(select(Payout)).first().id
    client.post(f"/payouts/{pid}/pay", data={"method": "cash"})
    client.post(f"/payouts/{pid}/reverse", data={"reason": "payment clawed back"})

    with database.SessionLocal() as s:
        team = s.scalars(select(Team).order_by(Team.id)).first()
        pt = _payout_totals(s, team.id)
        # Self-consistent breakdown, and the reversed amount is still counted as owed.
        assert pt["generated"] == pt["paid"] + pt["unpaid"] + pt["reversed"] + pt["held"]
        assert pt["outstanding"] == pt["unpaid"] + pt["reversed"] + pt["held"]
        assert pt["reversed"] > 0

    r = client.post("/reports/settlement-package")
    recon = zipfile.ZipFile(io.BytesIO(r.content)).read("reconciliation.txt").decode("utf-8")
    assert "Reversed" in recon and "Still owed" in recon
