"""HTTP-level test of the full golden path through the API routes —
exactly the sequence the frontend performs."""

import pytest
from fastapi.testclient import TestClient

from pulseops_api.db import get_session
from pulseops_api.main import app


@pytest.fixture()
def client(seeded):
    session, ids = seeded

    def _override():
        yield session

    app.dependency_overrides[get_session] = _override
    with TestClient(app, headers={"Content-Type": "application/json"}) as c:
        yield c, ids
    app.dependency_overrides.clear()


def test_health(client):
    c, _ = client
    assert c.get("/health").json()["status"] == "ok"


def test_dashboard_shows_chiller3_as_top_risk(client):
    c, ids = client
    data = c.get("/api/dashboard").json()
    assert data["asset_count"] == 9  # 8 synthetic HVAC assets + the MetroPT replay APU
    assert data["risk_source"] == "mock_demo_v0"
    top = data["top_risk_assets"][0]
    assert top["tag"] == "CH-03"
    assert top["risk"] > 0.6
    assert any("vibration" in i["title"].lower() for i in data["active_incidents"])


def test_asset_detail_and_readings(client):
    c, ids = client
    asset = c.get(f"/api/assets/{ids['chiller3_id']}").json()
    assert asset["name"] == "Chiller-03"
    assert {s["kind"] for s in asset["sensors"]} >= {"vibration_rms", "discharge_pressure"}

    series = c.get(f"/api/assets/{ids['chiller3_id']}/readings?hours=72").json()
    vib = next(s for s in series if s["sensor"]["kind"] == "vibration_rms")
    values = [p["value"] for p in vib["points"]]
    assert values[-1] > 2 * values[0], "vibration anomaly must be visible in the series"


def test_full_golden_path_over_http(client):
    c, ids = client

    # 1. start investigation
    run = c.post(
        "/api/agent/runs", json={"asset_id": ids["chiller3_id"], "trigger": "api test"}
    ).json()
    run_id = run["id"]

    # 2. run to the approval gate
    run = c.post(f"/api/agent/runs/{run_id}/advance", json={"mode": "until_blocked"}).json()
    assert run["status"] == "awaiting_approval"
    assert [s["phase"] for s in run["steps"]] == ["observe", "diagnose", "plan"]
    diagnose = next(s for s in run["steps"] if s["phase"] == "diagnose")
    assert diagnose["output"]["hypotheses"][0]["cause"] == "compressor bearing wear"
    action = run["actions"][0]
    assert action["status"] == "proposed"

    # 3. approve (the human step)
    decided = c.post(
        f"/api/agent/actions/{action['id']}/approve", json={"decided_by": "ifzaal"}
    ).json()
    assert decided["status"] == "approved"

    # double-decision is rejected cleanly
    assert c.post(f"/api/agent/actions/{action['id']}/approve").status_code == 409

    # 4. resume to completion
    run = c.post(f"/api/agent/runs/{run_id}/advance", json={"mode": "until_blocked"}).json()
    assert run["status"] == "completed"
    assert run["verification"]["passed"] is True
    assert run["verification"]["risk_before"] > run["verification"]["risk_after"]

    # 5. postmortem visible in case memory
    cases = c.get("/api/case-memory?asset_type=chiller").json()
    assert any(x["source_run_id"] == run_id for x in cases)

    # 6. agent work order visible
    orders = c.get(f"/api/work-orders?asset_id={ids['chiller3_id']}").json()
    assert any(w["source"] == "agent" for w in orders)


def test_verification_endpoint_returns_the_latest_attempt(client):
    """A failed first attempt is replanned; the endpoint must report the
    second (current) verification, as the run detail does."""
    from pulseops_api.services import environment

    c, ids = client
    run_id = c.post("/api/agent/runs", json={"asset_id": ids["chiller3_id"]}).json()["id"]
    run = c.post(f"/api/agent/runs/{run_id}/advance", json={"mode": "until_blocked"}).json()

    environment.set_outcome_profile(ids["chiller3_id"], "failure")
    c.post(f"/api/agent/actions/{run['actions'][0]['id']}/approve", json={"decided_by": "ops"})
    run = c.post(f"/api/agent/runs/{run_id}/advance", json={"mode": "until_blocked"}).json()
    assert run["status"] == "awaiting_approval"  # replanned after the failed attempt

    environment.set_outcome_profile(ids["chiller3_id"], "success")
    retry = next(a for a in run["actions"] if a["status"] == "proposed")
    c.post(f"/api/agent/actions/{retry['id']}/approve", json={"decided_by": "ops"})
    run = c.post(f"/api/agent/runs/{run_id}/advance", json={"mode": "until_blocked"}).json()
    assert run["status"] == "completed"

    latest = c.get(f"/api/agent/runs/{run_id}/verification").json()
    assert latest["outcome"] == "passed"
    assert latest["outcome"] == run["verification"]["outcome"]
