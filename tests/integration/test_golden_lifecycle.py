"""Golden end-to-end lifecycle (TST-02): one test exercises the whole event.

setup -> CSV import -> wagers (cart) -> void -> close -> results -> generate
-> pay -> SETTLED -> settlement package -> backup + restore.
"""
from __future__ import annotations

import io
import zipfile

from sqlalchemy import func, select

from app import database
from app.models import Customer, Payout, Player, Team, Tournament, Wager
from app.models.enums import PayoutStatus, TournamentStatus
from app.services import backups as backup_service
from tests.conftest import place_wager


def _new_customer(client, name):
    client.post("/customers/new", data={"name": name})
    with database.SessionLocal() as s:
        return s.scalars(select(Customer).where(Customer.name == name)).first().id


def test_full_event_lifecycle(client, settings):
    # 1. Setup: tournament + two teams.
    client.post("/setup/new", data={
        "name": "Golden Open", "entry_price": "5.00", "club_percent": "15",
        "first_percent": "60", "second_percent": "30", "third_percent": "10"})
    client.post("/setup/teams", data={"team_name": "Front Nine"})
    client.post("/setup/teams", data={"team_name": "Back Nine"})

    # 2. Import players via CSV.
    csv = ("team,player\n"
           "Front Nine,Alice\nFront Nine,Bob\nFront Nine,Carol\n"
           "Back Nine,Dan\nBack Nine,Erin\nBack Nine,Fay\n")
    client.post("/setup/import-players", files={"file": ("p.csv", csv.encode(), "text/csv")})
    client.post("/setup/open")

    with database.SessionLocal() as s:
        teams = s.scalars(select(Team).order_by(Team.id)).all()
        team_players = {t.id: [p.id for p in s.scalars(
            select(Player).where(Player.team_id == t.id).order_by(Player.id)).all()] for t in teams}
    assert sum(len(v) for v in team_players.values()) == 6  # CSV imported all six

    # 3. Wagers via the cashier cart, on every placed player of both teams, plus
    #    an extra wager on a placed player so voiding one won't zero a placement.
    cust = _new_customer(client, "Big Backer")
    for tid, players in team_players.items():
        for pid in players:
            place_wager(client, cust, tid, pid, 3)
    tid0 = teams[0].id
    place_wager(client, cust, tid0, team_players[tid0][0], 3)  # extra on the eventual 1st-place player

    # 4. Void one wager and confirm totals drop by exactly its quantity.
    from app.services import teams as team_service
    with database.SessionLocal() as s:
        pre_entries, _ = team_service.team_totals(s, tid0)
        first = s.scalars(select(Wager).order_by(Wager.id)).first()
        wid, wqty = first.id, first.quantity
    client.post(f"/wagers/{wid}/void", data={"reason": "duplicate", "return_to": "/ledger"})
    with database.SessionLocal() as s:
        post_entries, _ = team_service.team_totals(s, tid0)
        assert post_entries == pre_entries - wqty

    # 5. Close + results for both teams.
    client.post("/setup/close")
    for tid, players in team_players.items():
        client.post(f"/results/{tid}/placements", data={
            "first_player_id": players[0], "second_player_id": players[1], "third_player_id": players[2]})
        client.post(f"/results/{tid}/generate")

    # 6. Pay every payout.
    with database.SessionLocal() as s:
        payout_ids = [p.id for p in s.scalars(select(Payout)).all()]
    assert payout_ids
    for pid in payout_ids:
        client.post(f"/payouts/{pid}/pay", data={"method": "cash"})

    # 7. Whole event is SETTLED, all paid.
    with database.SessionLocal() as s:
        t = s.scalars(select(Tournament)).first()
        assert t.status == TournamentStatus.SETTLED
        assert s.scalar(select(func.count(Payout.id)).where(Payout.status != PayoutStatus.PAID)) == 0

    # 8. Settlement package produced with the full record.
    r = client.post("/reports/settlement-package")
    z = zipfile.ZipFile(io.BytesIO(r.content))
    assert "reconciliation.txt" in z.namelist()
    assert any(n.startswith("database_backup/") for n in z.namelist())

    # 9. Backup + restore roundtrip: a post-backup change is undone by restore.
    client.post("/admin/backup")
    backup_name = backup_service.list_backups(settings.backup_dir)[0].name
    _new_customer(client, "After Backup")
    r = client.post("/admin/restore", data={"backup_name": backup_name}, follow_redirects=False)
    assert r.status_code == 303
    with database.SessionLocal() as s:
        assert s.scalar(select(Customer).where(Customer.name == "After Backup")) is None
        assert s.scalar(select(Customer).where(Customer.name == "Big Backer")) is not None
