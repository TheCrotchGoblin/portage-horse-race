"""Settlement package export (spec RPT-02 / BKP-03).

Bundles the permanent record of an event into one timestamped ZIP: every CSV
export, a plain-text reconciliation summary, a configuration snapshot, and a
fresh database backup.
"""
from __future__ import annotations

import json
import zipfile
from datetime import datetime
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import Settings
from app.formatting import cents_to_dollars
from app.models import Payout, Placement, Player, Team, Tournament
from app.models.enums import PayoutStatus
from app.services import backups, exports
from app.services import cash as cash_service
from app.services import teams as team_service


def _safe(name: str) -> str:
    cleaned = "".join(c for c in (name or "event") if c.isalnum() or c in " -_").strip()
    return cleaned.replace(" ", "_") or "event"


def _payout_totals(session: Session, team_id: int) -> dict:
    rows = session.execute(
        select(Payout.status, func.coalesce(func.sum(Payout.amount_cents), 0))
        .join(Placement, Payout.placement_id == Placement.id)
        .where(Placement.team_id == team_id)
        .group_by(Payout.status)
    ).all()
    by = {s: int(c) for s, c in rows}
    paid = by.get(PayoutStatus.PAID, 0)
    unpaid = by.get(PayoutStatus.UNPAID, 0)
    reversed_ = by.get(PayoutStatus.REVERSED, 0)
    held = by.get(PayoutStatus.HELD, 0)
    return {
        # generated = paid + unpaid + reversed + held  (self-consistent breakdown)
        "generated": paid + unpaid + reversed_ + held,
        "paid": paid,
        "unpaid": unpaid,
        "reversed": reversed_,
        "held": held,
        # money not currently in a winner's hands (still owed / to resolve)
        "outstanding": unpaid + reversed_ + held,
    }


def configuration_snapshot(session: Session, tournament: Tournament) -> dict:
    teams = session.scalars(select(Team).where(Team.tournament_id == tournament.id).order_by(Team.id)).all()
    return {
        "name": tournament.name,
        "event_date": tournament.event_date,
        "status": tournament.status,
        "entry_price_cents": tournament.entry_price_cents,
        "club_bps": tournament.club_bps,
        "split_bps": [tournament.first_bps, tournament.second_bps, tournament.third_bps],
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "app_version": __import__("app").__version__,
        "teams": [
            {
                "name": t.name,
                "wagering_status": t.wagering_status,
                "players": session.scalar(select(func.count(Player.id)).where(Player.team_id == t.id)) or 0,
            }
            for t in teams
        ],
    }


def reconciliation_text(session: Session, tournament: Tournament) -> str:
    lines = [
        f"SETTLEMENT RECONCILIATION — {tournament.name}",
        f"Generated {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        "=" * 56,
        "",
    ]
    grand = {k: 0 for k in ("gross", "club", "pool", "generated", "paid", "unpaid", "reversed", "held", "outstanding")}
    teams = session.scalars(select(Team).where(Team.tournament_id == tournament.id).order_by(Team.id)).all()
    for team in teams:
        fin = team_service.team_financials(session, team.id, tournament)
        pt = _payout_totals(session, team.id)
        lines += [
            f"{team.name}",
            f"  Gross sales        {cents_to_dollars(fin.gross_cents):>14}",
            f"  Club share         {cents_to_dollars(fin.club_share_cents):>14}",
            f"  Prize pool         {cents_to_dollars(fin.prize_pool_cents):>14}",
            f"    1st / 2nd / 3rd  {cents_to_dollars(fin.first_pool_cents)} / {cents_to_dollars(fin.second_pool_cents)} / {cents_to_dollars(fin.third_pool_cents)}",
            f"  Payouts generated  {cents_to_dollars(pt['generated']):>14}",
            f"    Paid             {cents_to_dollars(pt['paid']):>14}",
            f"    Unpaid           {cents_to_dollars(pt['unpaid']):>14}",
        ]
        if pt["reversed"]:
            lines.append(f"    Reversed         {cents_to_dollars(pt['reversed']):>14}")
        if pt["held"]:
            lines.append(f"    Held             {cents_to_dollars(pt['held']):>14}")
        lines += [
            f"    Still owed       {cents_to_dollars(pt['outstanding']):>14}  (unpaid + reversed + held)",
            f"  Check (club+pool)  {cents_to_dollars(fin.club_share_cents + fin.prize_pool_cents):>14}  "
            + ("BALANCED" if fin.club_share_cents + fin.prize_pool_cents == fin.gross_cents else "REVIEW"),
            "",
        ]
        for k in grand:
            grand[k] += fin.gross_cents if k == "gross" else (
                fin.club_share_cents if k == "club" else (
                    fin.prize_pool_cents if k == "pool" else pt.get(k, 0)))

    lines += [
        "-" * 56,
        "ALL TEAMS",
        f"  Total gross        {cents_to_dollars(grand['gross']):>14}",
        f"  Total club share   {cents_to_dollars(grand['club']):>14}",
        f"  Total prize pools  {cents_to_dollars(grand['pool']):>14}",
        f"  Payouts generated  {cents_to_dollars(grand['generated']):>14}",
        f"  Paid               {cents_to_dollars(grand['paid']):>14}",
        f"  Still owed         {cents_to_dollars(grand['outstanding']):>14}  (unpaid {cents_to_dollars(grand['unpaid'])}"
        + (f" + reversed {cents_to_dollars(grand['reversed'])}" if grand['reversed'] else "")
        + (f" + held {cents_to_dollars(grand['held'])}" if grand['held'] else "") + ")",
        f"  Balanced?          {'YES' if grand['club'] + grand['pool'] == grand['gross'] else 'REVIEW'}",
    ]

    # Cash-drawer roll-up: float + cash in - cash paid out = cash on hand.
    float_total = sum(cash_service.opening_float(session, t.id) for t in teams)
    cash_paid = sum(cash_service.cash_paid_out(session, t.id) for t in teams)
    expected_cash = float_total + grand["gross"] - cash_paid
    lines += [
        "",
        "  CASH BOX (all teams)",
        f"    Opening float    {cents_to_dollars(float_total):>14}",
        f"    Cash taken in    {cents_to_dollars(grand['gross']):>14}",
        f"    Cash paid out    {cents_to_dollars(cash_paid):>14}",
        f"    Should be on hand{cents_to_dollars(expected_cash):>14}",
    ]
    return "\n".join(lines) + "\n"


def _open_me_html(tournament: Tournament, fingerprint: str, files: dict[str, bytes]) -> str:
    rows = "\n".join(
        f"      <tr><td><code>{name}</code></td><td>{_FILE_BLURB.get(name, '')}</td></tr>"
        for name in sorted(files) if name not in ("OPEN_ME.html",)
    )
    return f"""<!doctype html><html><head><meta charset="utf-8"><title>{tournament.name} — settlement package</title>
<style>body{{font-family:Segoe UI,system-ui,sans-serif;max-width:760px;margin:2rem auto;padding:0 1rem;color:#1b2430}}
h1{{color:#14532d}} code{{background:#f0fdf4;padding:1px 4px;border-radius:3px}}
table{{border-collapse:collapse;width:100%}} td,th{{border-bottom:1px solid #e6e8eb;padding:.4rem;text-align:left}}
.fp{{font-family:monospace;background:#f4f6f5;padding:.5rem;border-radius:6px}}</style></head>
<body>
  <h1>{tournament.name}</h1>
  <p>This ZIP is the permanent record of the horse race. Everything you need to review or
  reconcile the event is inside — it opens in a normal web browser and needs no internet.</p>
  <p><strong>What's here</strong></p>
  <table><thead><tr><th>File</th><th>What it is / who needs it</th></tr></thead>
    <tbody>
{rows}
    </tbody></table>
  <p><strong>Integrity fingerprint:</strong></p>
  <p class="fp">{fingerprint}</p>
  <p>This short code is printed on the reconciliation report. If both match, the signed paper
  ties to this exact digital bundle. <code>MANIFEST.sha256</code> lists a checksum for every file.</p>
</body></html>
"""


_FILE_BLURB = {
    "reconciliation.txt": "The money summary — totals, payouts and the cash box. Start here.",
    "configuration.json": "The event's settings (price, split, teams) at settlement time.",
    "customers.csv": "Everyone who placed a wager, with contact details. Sensitive.",
    "wagers.csv": "Every wager (the full ledger), including voids.",
    "player_totals.csv": "Entries and gross per player.",
    "payouts.csv": "Every winner payout and how it was settled.",
    "audit_log.csv": "The tamper-evident trail of every material action.",
    "MANIFEST.sha256": "A checksum for each file, to prove nothing was altered.",
}


def build_settlement_package(session: Session, tournament: Tournament, settings: Settings) -> Path:
    import hashlib

    settings.export_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    dest = settings.export_dir / f"settlement_{_safe(tournament.name)}_{ts}.zip"

    backup = backups.backup_database(settings.db_path, settings.backup_dir, reason="settlement_package")

    # Gather every content file as bytes so we can checksum and fingerprint them.
    files: dict[str, bytes] = {
        "configuration.json": json.dumps(configuration_snapshot(session, tournament), indent=2, default=str).encode("utf-8"),
        "customers.csv": exports.customers_csv(session).encode("utf-8"),
        "wagers.csv": exports.wagers_csv(session, tournament.id).encode("utf-8"),
        "player_totals.csv": exports.player_totals_csv(session, tournament.id).encode("utf-8"),
        "payouts.csv": exports.payouts_csv(session, tournament.id).encode("utf-8"),
        "audit_log.csv": exports.audit_csv(session, tournament.id).encode("utf-8"),
    }
    backup_arcname = None
    if backup and backup.exists():
        backup_arcname = f"database_backup/{backup.name}"
        files[backup_arcname] = backup.read_bytes()

    # A short fingerprint over all content files (excluding reconciliation.txt,
    # which will quote it) — deterministic and order-independent.
    digest = hashlib.sha256()
    for name in sorted(files):
        digest.update(name.encode("utf-8"))
        digest.update(hashlib.sha256(files[name]).digest())
    fingerprint = digest.hexdigest()[:16]

    recon = reconciliation_text(session, tournament)
    recon += f"\nPackage fingerprint: {fingerprint}\n"
    files["reconciliation.txt"] = recon.encode("utf-8")

    # MANIFEST covers every file, including the final reconciliation.txt.
    manifest_lines = [f"{hashlib.sha256(files[name]).hexdigest()}  {name}" for name in sorted(files)]
    files["MANIFEST.sha256"] = ("\n".join(manifest_lines) + "\n").encode("utf-8")
    files["OPEN_ME.html"] = _open_me_html(tournament, fingerprint, files).encode("utf-8")

    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as z:
        for name in sorted(files):
            z.writestr(name, files[name])
    return dest
