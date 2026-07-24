"""Administrator PIN recovery: recovery code and the reset-pin file fallback."""
from __future__ import annotations

import re

from app import database
from app.routes.deps import has_admin_pin


def _set_pin(client, pin="1234"):
    r = client.post("/admin/settings", data={"new_pin": pin}, follow_redirects=True)
    m = re.search(r"RECOVERY CODE: (\d{4}-\d{4})", r.text)
    assert m, "recovery code should be shown when a PIN is set"
    return m.group(1)


def test_recovery_code_clears_pin(client):
    code = _set_pin(client)
    with database.SessionLocal() as s:
        assert has_admin_pin(s)
    r = client.post("/admin/forgot-pin", data={"recovery_code": code}, follow_redirects=True)
    assert "has been cleared" in r.text
    with database.SessionLocal() as s:
        assert not has_admin_pin(s)


def test_recovery_code_also_accepts_no_dashes(client):
    code = _set_pin(client).replace("-", "")
    r = client.post("/admin/forgot-pin", data={"recovery_code": code}, follow_redirects=True)
    assert "has been cleared" in r.text


def test_wrong_recovery_code_rejected(client):
    _set_pin(client)
    r = client.post("/admin/forgot-pin", data={"recovery_code": "0000-0000"}, follow_redirects=True)
    assert "not correct" in r.text.lower()
    with database.SessionLocal() as s:
        assert has_admin_pin(s)  # still protected


def test_reset_pin_file_clears_pin(client, settings):
    _set_pin(client)
    (settings.data_dir / "reset-pin.txt").write_text("", encoding="utf-8")
    from app.main import _check_pin_reset

    _check_pin_reset(settings)
    with database.SessionLocal() as s:
        assert not has_admin_pin(s)
    assert not (settings.data_dir / "reset-pin.txt").exists()  # file consumed
