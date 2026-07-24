"""Drive the running app with headless Chromium and build a quickstart GIF.

Prereq: the app is running on BASE with an EMPTY database (so the welcome/setup
frames are captured first). The script then seeds data over HTTP and drives the
cart-based cashier for the remaining frames.

Run:  python packaging/make_demo_gif.py   ->  docs/quickstart.gif
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


def _players_by_team() -> dict[int, list[int]]:
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    rows = con.execute("SELECT id, team_id FROM players ORDER BY id").fetchall()
    con.close()
    out: dict[int, list[int]] = {}
    for pid, tid in rows:
        out.setdefault(tid, []).append(pid)
    return out


def seed(client: httpx.Client) -> None:
    client.post("/setup/new", data={
        "name": "Portage Men's Open 2026", "event_date": "2026-07-25", "entry_price": "5.00",
        "club_percent": "15", "first_percent": "60", "second_percent": "30", "third_percent": "10"})
    con = None
    for name in TEAMS:
        client.post("/setup/teams", data={"team_name": name})
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    team_rows = con.execute("SELECT id, name FROM teams ORDER BY id").fetchall()
    con.close()
    for tid, name in team_rows:
        client.post(f"/setup/teams/{tid}/players", data={"players": "\n".join(PLAYERS[name])})
    client.post("/setup/open")
    for c in CUSTOMERS:
        client.post("/customers/new", data={"name": c})

    # Seed a spread of wagers via the cart flow so the dashboard looks alive.
    by_team = _players_by_team()
    team_ids = sorted(by_team)
    plan = [
        (1, 0, 0, 8), (2, 0, 1, 4), (3, 0, 2, 12), (4, 1, 0, 10),
        (5, 1, 1, 6), (1, 1, 2, 20), (2, 0, 3, 5), (3, 1, 3, 9),
    ]
    for cust_id, team_idx, player_idx, qty in plan:
        tid = team_ids[team_idx]
        pid = by_team[tid][player_idx]
        client.get(f"/cashier?customer_id={cust_id}")
        client.post("/cashier/cart/add", data={"team_id": tid, "player_id": pid, "quantity": str(qty)})
        client.post("/cashier/checkout", data={"received": ""})


def main() -> None:
    FRAMES.mkdir(exist_ok=True)
    OUT.parent.mkdir(exist_ok=True)
    frames: list[tuple[Path, float]] = []

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport=VIEWPORT)

        def shot(name: str, seconds: float, hold: float = 0.4) -> None:
            time.sleep(hold)
            path = FRAMES / f"{name}.png"
            page.screenshot(path=str(path))
            frames.append((path, seconds))

        # 1) Welcome + 2) setup form (empty database).
        page.goto(f"{BASE}/", wait_until="networkidle")
        shot("01_welcome", 1.8)
        page.goto(f"{BASE}/setup/new", wait_until="networkidle")
        shot("02_setup", 2.0)

        with httpx.Client(base_url=BASE, follow_redirects=True, timeout=30) as c:
            seed(c)

        # 3) Setup overview (teams, players, settings).
        page.goto(f"{BASE}/setup", wait_until="networkidle")
        shot("03_setup_overview", 2.0)

        # 4) Home hub for the active tournament.
        page.goto(f"{BASE}/", wait_until="networkidle")
        shot("04_home", 1.8)

        # 5) Cashier — build an order across two players, then it's ready to pay.
        page.goto(f"{BASE}/cashier?customer_id=1", wait_until="networkidle")
        add_forms = page.locator('form[action="/cashier/cart/add"]')
        f0 = add_forms.nth(0)
        f0.locator('input[name="quantity"]').fill("3")
        f0.locator("button").click()
        page.wait_for_load_state("networkidle")
        add_forms = page.locator('form[action="/cashier/cart/add"]')
        f1 = add_forms.nth(2)
        f1.locator('input[name="quantity"]').fill("2")
        f1.locator("button").click()
        page.wait_for_load_state("networkidle")
        shot("05_cashier", 2.6)

        # 6) Dashboard with live team totals.
        page.goto(f"{BASE}/dashboard", wait_until="networkidle")
        shot("06_dashboard", 2.4)

        # 7) Reports.
        page.goto(f"{BASE}/reports", wait_until="networkidle")
        shot("07_reports", 2.2)

        browser.close()

    images, durations = [], []
    for path, seconds in frames:
        im = Image.open(path).convert("RGB")
        w = 1040
        im = im.resize((w, int(im.height * w / im.width)), Image.LANCZOS)
        images.append(im.convert("P", palette=Image.ADAPTIVE, colors=200))
        durations.append(int(seconds * 1000))

    images[0].save(OUT, save_all=True, append_images=images[1:], duration=durations,
                   loop=0, optimize=True, disposal=2)
    print(f"wrote {OUT} ({OUT.stat().st_size // 1024} KB, {len(images)} frames)")


if __name__ == "__main__":
    main()
