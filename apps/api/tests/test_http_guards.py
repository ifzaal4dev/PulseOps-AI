"""Request-level guards: JSON-only writes and the demo-reset switch."""

import pytest
from fastapi.testclient import TestClient

from pulseops_api import config
from pulseops_api.db import get_session
from pulseops_api.main import app


@pytest.fixture()
def raw_client(seeded):
    """A client with no default headers, so each test controls Content-Type."""
    session, ids = seeded

    def _override():
        yield session

    app.dependency_overrides[get_session] = _override
    with TestClient(app) as client:
        yield client, ids
    app.dependency_overrides.clear()


def proposed_action_id(client, asset_id):
    run_id = client.post("/api/agent/runs", json={"asset_id": asset_id}).json()["id"]
    run = client.post(f"/api/agent/runs/{run_id}/advance", json={"mode": "until_blocked"}).json()
    return run["actions"][0]["id"]


def test_bodyless_or_form_writes_are_rejected(raw_client):
    """Simple cross-site requests (no body, or a form) must not reach the
    approval endpoint."""
    client, ids = raw_client
    action_id = proposed_action_id(client, ids["chiller3_id"])

    assert client.post(f"/api/agent/actions/{action_id}/approve").status_code == 415
    form = client.post(f"/api/agent/actions/{action_id}/approve", data={"decided_by": "attacker"})
    assert form.status_code == 415

    action = next(a for a in client.get("/api/agent/actions").json() if a["id"] == action_id)
    assert action["status"] == "proposed"


def test_json_writes_and_reads_are_unaffected(raw_client):
    client, ids = raw_client
    action_id = proposed_action_id(client, ids["chiller3_id"])
    approved = client.post(
        f"/api/agent/actions/{action_id}/approve", json={"decided_by": "operator"}
    )
    assert approved.status_code == 200
    assert client.get("/health").status_code == 200


def test_demo_reset_can_be_disabled(raw_client, monkeypatch):
    client, _ = raw_client
    monkeypatch.setattr(config.settings, "enable_demo_reset", False)
    response = client.post("/api/demo/reset", json={"confirm": "RESET"})
    assert response.status_code == 403
