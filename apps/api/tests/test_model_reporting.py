"""The portfolio UI's ML claims must stay tied to generated evidence."""

import json

import pytest

from pulseops_api.services.model_reporting import (
    ModelReportError,
    load_flagship_evaluation,
)


def test_committed_flagship_report_is_loadable_and_honest():
    report = load_flagship_evaluation()
    assert report["model_source"] == "metropt3_iforest_v1"
    assert report["primary_policy"]["policy"] == "iforest_global"
    assert report["primary_policy"]["events_detected"] == 3
    assert report["events_total"] == 4
    assert report["trigger_mode"] == "shadow"
    assert report["readiness_blockers"]
    assert all(
        event["lead_time_hours"] is None or event["lead_time_hours"] < 0
        for event in report["primary_policy"]["per_event"]
    )


def test_committed_report_carries_chronological_presentation_data():
    report = load_flagship_evaluation()
    episodes = report["false_alarm_episode_intervals"]
    # The intervals ARE the counted episodes — presentation must match metrics.
    assert len(episodes) == report["primary_policy"]["false_alarm_episodes"]
    assert all(e["start"] <= e["end"] and e["n_alert_windows"] >= 1 for e in episodes)
    by_mode = report["monthly_mode_alert_rate"]
    assert set(by_mode) == set(report["monthly_alert_rate"])
    for row in by_mode.values():
        assert set(row) == {"loaded", "idle"}
    # Alerting concentrates in loaded operation: summer loaded windows alert
    # often, idle windows don't.
    june = by_mode["2020-06"]
    assert june["loaded"]["alert_rate"] > 0.5 and june["idle"]["alert_rate"] == 0.0
    train = report["training_mode_windows"]
    assert train["loaded"] < train["idle"]


def test_committed_report_scores_the_policy_the_app_runs():
    """The app only alerts after persistent crossings. The report must carry
    that policy alongside the single-window primary, on the same threshold."""
    report = load_flagship_evaluation()
    persistent = report["persistent_policy"]
    primary = report["primary_policy"]
    assert persistent["policy"] == "iforest_global_persistent"
    assert persistent["threshold"].startswith(primary["threshold"])
    assert persistent["events_detected"] <= primary["events_detected"] + 1
    assert persistent["false_alarm_episodes"] <= primary["false_alarm_episodes"]
    # A calendar span overstates observed time when the data has gaps.
    assert report["observed_days"] < report["eligible_days"]
    assert primary["false_alarms_per_observed_day"] > primary["false_alarms_per_operating_day"]
    assert report["trigger_mode"] == "shadow"


def test_trigger_policy_endpoint_reports_runtime_constants():
    from fastapi.testclient import TestClient

    from pulseops_api.main import app
    from pulseops_api.services import shadow_trigger

    with TestClient(app, headers={"Content-Type": "application/json"}) as client:
        body = client.get("/api/trigger-policy").json()
    assert body["persistence_windows"] == shadow_trigger.PERSISTENCE_WINDOWS
    assert body["hysteresis_low_factor"] == shadow_trigger.LOW_FACTOR
    assert body["cooldown_minutes"] == shadow_trigger.COOLDOWN_MINUTES
    assert body["mode"] == "shadow"
    assert "readiness bar" in body["explanation"]


def test_demo_reset_requires_explicit_confirmation():
    from fastapi.testclient import TestClient

    from pulseops_api.main import app

    with TestClient(app, headers={"Content-Type": "application/json"}) as client:
        denied = client.post("/api/demo/reset", json={"confirm": "yes"})
    assert denied.status_code == 400
    assert "RESET" in denied.json()["detail"]


def test_report_loader_rejects_missing_primary_provenance(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text(
        json.dumps(
            {
                "generated_at": "now",
                "task": "test",
                "model_source": "m",
                "feature_version": "f",
                "policies": [],
                "trigger_decision": {"model_source": "different"},
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ModelReportError, match="primary-policy provenance"):
        load_flagship_evaluation(path)
