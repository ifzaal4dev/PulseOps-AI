"""The offline evaluation's trigger replica must behave exactly like the app.

pulseops_ml.trigger.simulate_shadow_trigger reports how the app's alerting
policy performs on the holdout data. If it drifts from
services/shadow_trigger.py, the published numbers describe a policy the app
doesn't run.
"""

from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from pulseops_api.models import ModelPrediction, ReplayRun, ShadowAlert
from pulseops_api.services import shadow_trigger
from pulseops_ml import trigger

THRESHOLD = 0.5
START = datetime(2020, 6, 5, 10, 0, tzinfo=UTC)

# Each value is one 10-minute window's score (low threshold = 0.4).
SCORES = [
    0.6,  # isolated crossing
    0.3,
    0.6,
    0.6,  # two in a row: not enough
    0.3,
    0.6,
    0.6,
    0.6,  # third consecutive crossing: episode opens
    0.45,  # below threshold but above the low line: stays open
    0.6,
    0.35,  # below the low line: clears, cooldown starts
    0.6,
    0.6,
    0.6,  # a full streak inside the cooldown: blocked
    0.6,
    0.6,
    0.6,  # first window at the cooldown boundary: opens again
    0.2,
]


def test_rules_and_constants_match_the_app():
    assert trigger.PERSISTENCE == shadow_trigger.PERSISTENCE_WINDOWS
    assert trigger.LOW_FACTOR == shadow_trigger.LOW_FACTOR
    assert trigger.COOLDOWN_MINUTES == shadow_trigger.COOLDOWN_MINUTES


def test_simulator_opens_and_holds_episodes_like_the_app(seeded):
    session, ids = seeded
    times = [START + timedelta(minutes=10 * i) for i in range(len(SCORES))]
    run = ReplayRun(
        dataset_name="metropt3",
        dataset_checksum="fixture",
        asset_id=ids["apu_id"],
        source_start=times[0],
        source_end=times[-1],
        speed=3600.0,
        status="running",
        model_source="metropt3_iforest_v1",
        feature_version="metropt3_fw_v1",
    )
    session.add(run)
    session.flush()

    app_opened, app_active = [], []
    for t, score in zip(times, SCORES, strict=True):
        before = {a.id for a in session.scalars(select(ShadowAlert))}
        prediction = ModelPrediction(
            replay_run_id=run.id,
            asset_id=ids["apu_id"],
            window_end=t,
            risk=score,
            threshold=THRESHOLD,
            alert=score >= THRESHOLD,
            shadow=True,
            model_source="metropt3_iforest_v1",
            policy_mode="shadow",
            policy_version="parity",
        )
        session.add(prediction)
        session.flush()
        open_alert = shadow_trigger.process_prediction(session, prediction)
        if any(a.id not in before for a in session.scalars(select(ShadowAlert))):
            app_opened.append(t)
        app_active.append(open_alert is not None)

    episodes, sim_active = trigger.simulate_shadow_trigger(times, SCORES, THRESHOLD)

    assert [e.opened_at for e in episodes] == app_opened
    assert sim_active == app_active
    assert app_opened == [times[7], times[16]]  # the scenario exercises both paths
