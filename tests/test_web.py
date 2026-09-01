"""
Unit tests for FastAPI Web Dashboard endpoints.
"""

from fastapi.testclient import TestClient
from web.app import app
from web.state import dashboard_state
from indicators.pivots import calculate_daily_pivots
from scanner.signal_engine import SignalEvent
from datetime import datetime


def test_web_endpoints():
    client = TestClient(app)

    # 1. Initialize State
    pivots = {
        "RELIANCE": calculate_daily_pivots("RELIANCE", "2026-08-31", 1280.0, 1300.0, 1270.0, 1290.0, 1000000),
    }
    dashboard_state.initialize_pivots(pivots)

    # 2. Add a sample signal
    sig = SignalEvent(
        symbol="RELIANCE",
        timestamp=datetime(2026, 9, 1, 12, 5),
        direction="BULLISH SETUP",
        pattern="BULLISH ENGULFING",
        price=1308.40,
        score=7,
        score_breakdown=["Pattern (+3)", "Pivot (+4)"],
        zone="PP - R1 (Bullish Bias)",
        pivot=1281.87,
        pdo=1280.0,
        pdh=1297.60,
        pdl=1271.00,
        pdc=1277.00,
        pdv=1000000,
        r1=1292.73,
        r2=1308.47,
        r3=1319.33,
        s1=1266.13,
        s2=1255.27,
        s3=1239.53,
        volume=50000,
        relative_volume=1.5,
        conditions_met=["Price > Pivot", "Price > PDH"],
    )
    dashboard_state.add_signal(sig)

    # 3. Test Index HTML route
    resp = client.get("/")
    assert resp.status_code == 200
    assert "UPSTOX 5M SCANNER" in resp.text
    assert "Active Signals Stream" in resp.text

    # 4. Test Snapshot JSON API
    resp_snap = client.get("/api/snapshot")
    assert resp_snap.status_code == 200
    data = resp_snap.json()
    assert "market" in data
    assert "signals" in data
    assert len(data["signals"]) >= 1
    assert data["signals"][0]["symbol"] == "RELIANCE"

    # 5. Test Stats API
    resp_stats = client.get("/api/stats")
    assert resp_stats.status_code == 200
    stats = resp_stats.json()
    assert stats["symbols_scanned"] >= 1
