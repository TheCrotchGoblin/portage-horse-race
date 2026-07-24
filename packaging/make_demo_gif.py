"""Drive the running app with headless Chromium and build a quickstart GIF.

Prereq: the app is running on BASE (empty database) so the welcome/setup frames
are captured first, then the script seeds data over HTTP and captures the rest.

Run:  python packaging/make_demo_gif.py
Output: docs/quickstart.gif
"""
from __future__ import annotations

import sqlite3
import time
from pathlib import Path

import httpx
from PIL import Image
from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8810"
ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / ".gifdemo" / "db" / "horse_race.sqlite3"
FRAMES = ROOT / ".gifframes"
OUT = ROOT / "docs" / "quickstart.gif"

VIEWPORT = {"width": 1280, "height": 860}
TEAMS = ["Front Nine", "Back Nine"]
PLAYERS = {
    "Front Nine": ["Mike Hansen", "Dave Carroll", "Steve Miller", "Tom Blythe", "Jay Okafor", "Rob Friesen"],
    "Back Nine": ["Chris Nolan", "Pat Reilly", "Sam Doyle", "Alex Kerr", "Neil Watts", "Gord Ives"],
}
CUSTOMERS = ["Linda Marsh", "Kevin Poole", "Sarah Tran", "Doug Reimer", "Wendy Lang"]


def seed(client: httpx.Client) -> None:
    client.post("/setup/new", data={
        "name": "Portage Men's Open 2026", "event_date": "2026-07-25", "entry_price": "5.00",
        "club_percent": "15", "first_percent": "60", "second_percent": "30", "third_percent": "10"})
    for name in TEAMS:
        client.post("/setup/teams", data={"team_name": name})
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    team_rows = con.execute("SELECT id, name FROM teams ORDER BY id").fetchall()
    con.close()
    team_ids = {name: tid for tid, name in team_rows}
    for name, tid in team_ids.items():
        client.post(f"/setup/teams/{tid}/players", data={"players": "\n".join(PLAYERS[name])})
    client.post("/setup/open")
    for c in CUSTOMERS:
        client.post("/customers/new", data={"name": c, "phone": ""})

    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    players = con.execute("SELECT id, team_id, name FROM players ORDER BY id").fetchall()
    customers = [r[0] for r in con.execute("SELECT id FROM customers ORDER BY id").fetchall()]
    con.close()

    # A spread of wagers so the dashboard looks alive.
    plan = [
        (0, 0, 8), (1, 1, 4), (2, 2, 12), (3, 0, 6), (4, 3, 10),
        (0, 6, 5), (1, 7, 3), (2, 8, 20), (3, 9, 7), (4, 6, 9), (0, 10, 4),
    ]
    for cust_idx, player_idx, qty in plan:
        pid, team_id, _ = players[player_idx]
        client.post("/wagers", data={
            "customer_id": customers[cust_idx], "team_id": team_id, "player_id": pid, "quantity": str(qty)})


def read_ids() -> dict:
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    cust = con.execute("SELECT id FROM customers ORDER BY id").fetchone()[0]
    # Team B (second) and a player on it for the cashier deep-link.
    team = con.execute("SELECT id FROM teams ORDER BY id").fetchall()[1][0]
    player = con.execute("SELECT id FROM players WHERE team_id=? ORDER BY id", (team,)).fetchall()[2][0]
    con.close()
    return {"customer": cust, "team": team, "player": player}


def main() -> None:
    FRAMES.mkdir(exist_ok=True)
    OUT.parent.mkdir(exist_ok=True)
    frames: list[tuple[Path, float]] = []

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport=VIEWPORT, device_scale_factor=1)

        def shot(name: str, seconds: float, hold: float = 0.4) -> None:
            time.sleep(hold)
            path = FRAMES / f"{name}.png"
            page.screenshot(path=str(path))
            frames.append((path, seconds))

        # 1) Welcome + 2) setup form (empty database).
        page.goto(f"{BASE}/", wait_until="networkidle")
        shot("01_welcome", 1.8)
        page.goto(f"{BASE}/setup/new", wait_until="networkidle")
        page.fill("input[name=name]", "Portage Men's Open 2026")
        shot("02_setup", 2.0)

        # Seed data through the running server.
        with httpx.Client(base_url=BASE, follow_redirects=True, timeout=30) as c:
            seed(c)
        ids = read_ids()

        # 3) Dashboard populated.
        page.goto(f"{BASE}/", wait_until="networkidle")
        shot("03_dashboard", 2.2)

        # 4-6) Cashier click-through via deep links (customer -> team -> player+qty).
        cx = f"customer_id={ids['customer']}"
        page.goto(f"{BASE}/cashier?{cx}", wait_until="networkidle")
        shot("04_cashier_team", 1.5)
        page.goto(f"{BASE}/cashier?{cx}&team_id={ids['team']}", wait_until="networkidle")
        shot("05_cashier_player", 1.5)
        page.goto(f"{BASE}/cashier?{cx}&team_id={ids['team']}&player_id={ids['player']}&qty=5", wait_until="networkidle")
        shot("06_cashier_pay", 2.4)

        # 7) Live team totals / dashboard reconciliation again for the close.
        page.goto(f"{BASE}/reports", wait_until="networkidle")
        shot("07_reports", 2.2)

        browser.close()

    # Assemble the GIF (scaled to 1040px wide to keep the file lean).
    images = []
    durations = []
    for path, seconds in frames:
        im = Image.open(path).convert("RGB")
        w = 1040
        h = int(im.height * w / im.width)
        im = im.resize((w, h), Image.LANCZOS)
        images.append(im.convert("P", palette=Image.ADAPTIVE, colors=200))
        durations.append(int(seconds * 1000))

    images[0].save(
        OUT, save_all=True, append_images=images[1:], duration=durations, loop=0, optimize=True, disposal=2
    )
    print(f"wrote {OUT} ({OUT.stat().st_size // 1024} KB, {len(images)} frames)")


if __name__ == "__main__":
    main()
