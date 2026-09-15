from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = (ROOT / "backend" / "app.py").read_text(encoding="utf-8")
FRONT = (ROOT / "frontend" / "src" / "main.jsx").read_text(encoding="utf-8")

def test_refresh_rotation_and_revocation_present():
    assert "RefreshSession" in APP
    assert "session.revoked_at = now()" in APP
    assert "Password changed; active refresh sessions revoked" in APP

def test_frontend_refresh_does_not_retry_forever():
    assert "performRefresh()" in FRONT
    assert "request(path,opts,false)" in FRONT
    assert "retry=true" in FRONT

def test_frontend_does_not_use_async_useeffect_directly():
    assert "useEffect(async" not in FRONT.replace(" ", "")
