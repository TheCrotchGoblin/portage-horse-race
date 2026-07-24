"""SQLAlchemy ORM models (spec §9).

Money is stored as integer cents; percentages as basis points (1500 = 15.00%).
Financial rows are never hard-deleted — corrections happen via void + audit.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.enums import (
    PayoutStatus,
    Position,
    TournamentStatus,
    WageringStatus,
    WagerStatus,
)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Tournament(Base):
    __tablename__ = "tournaments"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    event_date: Mapped[str | None] = mapped_column(String(32))  # ISO date string
    status: Mapped[str] = mapped_column(String(32), default=TournamentStatus.DRAFT, nullable=False)

    entry_price_cents: Mapped[int] = mapped_column(Integer, default=500, nullable=False)
    club_bps: Mapped[int] = mapped_column(Integer, default=1500, nullable=False)
    first_bps: Mapped[int] = mapped_column(Integer, default=6000, nullable=False)
    second_bps: Mapped[int] = mapped_column(Integer, default=3000, nullable=False)
    third_bps: Mapped[int] = mapped_column(Integer, default=1000, nullable=False)

    created_at: Mapped[datetime] = mapped_column(default=utcnow, nullable=False)

    teams: Mapped[list["Team"]] = relationship(back_populates="tournament", cascade="all, delete-orphan")

    __table_args__ = (
        CheckConstraint("entry_price_cents >= 0", name="ck_tournament_price_nonneg"),
        CheckConstraint("club_bps >= 0 AND club_bps <= 10000", name="ck_tournament_club_bps"),
        CheckConstraint("first_bps + second_bps + third_bps = 10000", name="ck_tournament_split_100"),
    )


class Team(Base):
    __tablename__ = "teams"

    id: Mapped[int] = mapped_column(primary_key=True)
    tournament_id: Mapped[int] = mapped_column(ForeignKey("tournaments.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    wagering_status: Mapped[str] = mapped_column(String(16), default=WageringStatus.CLOSED, nullable=False)

    tournament: Mapped[Tournament] = relationship(back_populates="teams")
    players: Mapped[list["Player"]] = relationship(back_populates="team", cascade="all, delete-orphan")

    __table_args__ = (Index("ix_teams_tournament", "tournament_id"),)


class Player(Base):
    __tablename__ = "players"

    id: Mapped[int] = mapped_column(primary_key=True)
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    display_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    active: Mapped[bool] = mapped_column(default=True, nullable=False)

    team: Mapped[Team] = relationship(back_populates="players")

    __table_args__ = (Index("ix_players_team", "team_id"),)


class Customer(Base):
    __tablename__ = "customers"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    phone_raw: Mapped[str | None] = mapped_column(String(40))
    phone_normalized: Mapped[str | None] = mapped_column(String(40))
    email: Mapped[str | None] = mapped_column(String(200))
    email_normalized: Mapped[str | None] = mapped_column(String(200))
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow, nullable=False)

    __table_args__ = (
        Index("ix_customers_phone_norm", "phone_normalized"),
        Index("ix_customers_email_norm", "email_normalized"),
        Index("ix_customers_name", "name"),
    )


class Wager(Base):
    __tablename__ = "wagers"

    id: Mapped[int] = mapped_column(primary_key=True)
    tournament_id: Mapped[int] = mapped_column(ForeignKey("tournaments.id"), nullable=False)
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"), nullable=False)
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id"), nullable=False)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"), nullable=False)

    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    unit_price_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    amount_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    received_cents: Mapped[int | None] = mapped_column(Integer)

    status: Mapped[str] = mapped_column(String(16), default=WagerStatus.ACTIVE, nullable=False)
    operator_id: Mapped[str | None] = mapped_column(String(120))
    note: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(default=utcnow, nullable=False)
    voided_at: Mapped[datetime | None] = mapped_column()
    void_reason: Mapped[str | None] = mapped_column(Text)
    replacement_for_id: Mapped[int | None] = mapped_column(ForeignKey("wagers.id"))

    customer: Mapped[Customer] = relationship()
    player: Mapped[Player] = relationship()
    team: Mapped[Team] = relationship()

    __table_args__ = (
        CheckConstraint("quantity > 0", name="ck_wager_qty_pos"),
        CheckConstraint("amount_cents >= 0", name="ck_wager_amount_nonneg"),
        CheckConstraint("unit_price_cents >= 0", name="ck_wager_price_nonneg"),
        Index("ix_wagers_team", "team_id"),
        Index("ix_wagers_player", "player_id"),
        Index("ix_wagers_customer", "customer_id"),
        Index("ix_wagers_status", "status"),
        Index("ix_wagers_created", "created_at"),
    )


class Placement(Base):
    __tablename__ = "placements"

    id: Mapped[int] = mapped_column(primary_key=True)
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)  # 1, 2 or 3
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id"), nullable=False)
    allocated_pool_cents: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    finalized_at: Mapped[datetime | None] = mapped_column()
    finalized_by: Mapped[str | None] = mapped_column(String(120))

    player: Mapped[Player] = relationship()

    __table_args__ = (
        UniqueConstraint("team_id", "position", name="uq_placement_team_position"),
        CheckConstraint("position IN (1, 2, 3)", name="ck_placement_position"),
        Index("ix_placements_team", "team_id"),
    )


class Payout(Base):
    __tablename__ = "payouts"

    id: Mapped[int] = mapped_column(primary_key=True)
    placement_id: Mapped[int] = mapped_column(ForeignKey("placements.id"), nullable=False)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"), nullable=False)
    winning_entries: Mapped[int] = mapped_column(Integer, nullable=False)
    amount_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(16), default=PayoutStatus.UNPAID, nullable=False)
    paid_at: Mapped[datetime | None] = mapped_column()
    paid_by: Mapped[str | None] = mapped_column(String(120))
    payment_method: Mapped[str | None] = mapped_column(String(40))
    note: Mapped[str | None] = mapped_column(Text)

    placement: Mapped[Placement] = relationship()
    customer: Mapped[Customer] = relationship()

    __table_args__ = (
        UniqueConstraint("placement_id", "customer_id", name="uq_payout_placement_customer"),
        CheckConstraint("amount_cents >= 0", name="ck_payout_amount_nonneg"),
        Index("ix_payouts_status", "status"),
    )


class CashCount(Base):
    __tablename__ = "cash_counts"

    id: Mapped[int] = mapped_column(primary_key=True)
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"), nullable=False)
    counted_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    counted_at: Mapped[datetime] = mapped_column(default=utcnow, nullable=False)
    counted_by: Mapped[str | None] = mapped_column(String(120))
    note: Mapped[str | None] = mapped_column(Text)


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    tournament_id: Mapped[int | None] = mapped_column(ForeignKey("tournaments.id"))
    actor: Mapped[str | None] = mapped_column(String(120))
    action_type: Mapped[str] = mapped_column(String(60), nullable=False)
    entity_type: Mapped[str | None] = mapped_column(String(60))
    entity_id: Mapped[str | None] = mapped_column(String(60))
    before_json: Mapped[str | None] = mapped_column(Text)
    after_json: Mapped[str | None] = mapped_column(Text)
    reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(default=utcnow, nullable=False)

    __table_args__ = (Index("ix_audit_created", "created_at"),)


class Setting(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    value: Mapped[str | None] = mapped_column(Text)
