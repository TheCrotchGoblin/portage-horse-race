"""Seed a clearly-labelled demo tournament so a first-timer can explore safely (UX-07).

The demo is a normal tournament named "DEMO — …"; it can be archived or deleted
like any other and never touches a real tournament (it is only offered when no
tournament is active).
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Player, Team, Tournament
from app.services import customers as customer_service
from app.services import setup as setup_service
from app.services import wagers as wager_service

DEMO_NAME = "DEMO — Sample Event"

_PLAYERS = {
    "Front Nine": ["Mike Hansen", "Dave Carroll", "Steve Miller", "Tom Blythe"],
    "Back Nine": ["Chris Nolan", "Pat Reilly", "Sam Doyle", "Alex Kerr"],
}
_CUSTOMERS = ["Linda Marsh", "Kevin Poole", "Sarah Tran", "Doug Reimer"]
# (customer index, team index, player index, quantity)
_WAGERS = [
    (0, 0, 0, 4), (1, 0, 1, 2), (2, 0, 2, 6), (3, 1, 0, 8),
    (0, 1, 1, 3), (1, 1, 2, 10), (2, 0, 0, 5), (3, 1, 3, 2),
]


def seed_demo(session: Session, operator: str = "demo") -> Tournament:
    tournament = setup_service.create_tournament(
        session, name=DEMO_NAME, event_date=None, entry_price_cents=500,
        club_bps=1500, first_bps=6000, second_bps=3000, third_bps=1000, operator=operator,
    )
    teams: list[Team] = []
    for name, players in _PLAYERS.items():
        team = setup_service.add_team(session, tournament, name)
        setup_service.add_players(session, team, players)
        teams.append(team)
    setup_service.open_wagering(session, tournament, operator)

    customers = [customer_service.create_customer(session, name=n) for n in _CUSTOMERS]
    team_players = {
        t.id: session.scalars(select(Player).where(Player.team_id == t.id).order_by(Player.id)).all()
        for t in teams
    }
    for ci, ti, pi, qty in _WAGERS:
        team = teams[ti]
        player = team_players[team.id][pi]
        wager_service.record_wager(
            session, tournament=tournament, team_id=team.id, player_id=player.id,
            customer_id=customers[ci].id, quantity=qty, operator=operator,
        )
    return tournament
