"""CSV exports, print views, and backup/restore roundtrip (spec §15.2)."""
from __future__ import annotations

from sqlalchemy import select

from app import database
from app.models import Customer, Player, Team, Wager
from app.services import backups as backup_service
from tests.conftest import make_tournament


def _ids(client):
    with database.SessionLocal() as s:
        team = s.scalars(select(Team).order_by(Team.id)).first()
        player = s.scalars(select(Player).where(Player.team_id == team.id).order_by(Player.id)).first()
        return team.id, player.id


def _new_customer(client, name):
    client.post("/customers/new", data={"name": name})
    with database.SessionLocal() as s:
        return s.scalars(select(Customer).where(Customer.name == name)).first().id


def test_csv_exports(client):
    make_tournament(client)
    team_id, player_id = _ids(client)
    cid = _new_customer(client, "Export Me")
    client.post("/wagers", data={"customer_id": cid, "team_id": team_id, "player_id": player_id, "quantity": "3"})

    r = client.get("/reports/export/wagers")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/csv")
    assert "wager_id" in r.text and "Export Me" in r.text and "15.00" in r.text

    for kind in ["customers", "player-totals", "payouts", "audit"]:
        assert client.get(f"/reports/export/{kind}").status_code == 200


def test_print_views_render(client):
    make_tournament(client)
    for kind in ["team-summary", "results", "payouts", "reconciliation"]:
        r = client.get(f"/reports/print/{kind}")
        assert r.status_code == 200
        assert "Test Open" in r.text


def test_results_report_discloses_remainder(client):
    """3rd pool $127.50 over 95 entries -> 20 remainder cents disclosed (BR-12 / §8.3)."""
    make_tournament(client)
    with database.SessionLocal() as s:
        team = s.scalars(select(Team).order_by(Team.id)).first()
        players = s.scalars(select(Player).where(Player.team_id == team.id).order_by(Player.id)).all()
        team_id = team.id
        alice, bob, carol = [p.id for p in players]
    cid = _new_customer(client, "R")
    client.post("/wagers", data={"customer_id": cid, "team_id": team_id, "player_id": alice, "quantity": "5"})
    client.post("/wagers", data={"customer_id": cid, "team_id": team_id, "player_id": bob, "quantity": "200"})
    client.post("/wagers", data={"customer_id": cid, "team_id": team_id, "player_id": carol, "quantity": "95"})
    client.post(f"/results/{team_id}/placements", data={
        "first_player_id": alice, "second_player_id": bob, "third_player_id": carol})

    r = client.get("/reports/print/results")
    assert r.status_code == 200
    assert "first 20 entrie" in r.text  # 12750 - 134*95 = 20 remainder cents


def test_backup_then_restore_drops_later_transaction(client):
    make_tournament(client)
    team_id, player_id = _ids(client)
    cid = _new_customer(client, "Baseline")
    client.post("/wagers", data={"customer_id": cid, "team_id": team_id, "player_id": player_id, "quantity": "1"})

    # Back up with the baseline in place.
    client.post("/admin/backup")
    settings = client.app.state.settings
    backups = backup_service.list_backups(settings.backup_dir)
    assert backups, "a backup file should exist"
    backup_name = backups[0].name

    # Add a later transaction that should NOT survive the restore.
    client.post("/wagers", data={"customer_id": cid, "team_id": team_id, "player_id": player_id, "quantity": "99"})
    with database.SessionLocal() as s:
        assert s.scalar(select(Wager).where(Wager.quantity == 99)) is not None

    # Restore the earlier backup.
    r = client.post("/admin/restore", data={"backup_name": backup_name}, follow_redirects=False)
    assert r.status_code == 303

    with database.SessionLocal() as s:
        # Later (qty 99) transaction is gone; baseline (qty 1) remains.
        assert s.scalar(select(Wager).where(Wager.quantity == 99)) is None
        assert s.scalar(select(Wager).where(Wager.quantity == 1)) is not None
        assert s.scalar(select(Customer).where(Customer.name == "Baseline")) is not None
