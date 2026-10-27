"""Shadow trigger state machine and operator promotion.

Honors ml/model_artifacts/trigger_policy.json: the MetroPT model is
EXPERIMENTAL, so the trigger rules run and their would-trigger decisions are
durably persisted as ShadowAlert rows — but NO Incident and NO AgentRun is
ever created automatically, regardless of how many predictions alert. The
only path from a shadow alert to an incident is an explicit operator
promotion, which creates a clearly-labeled SYNTHETIC/DEMO incident carrying
full model/policy provenance. Promotion is NOT maintenance approval — any
later work order still follows propose → human approve → execute.

Trigger rules (documented policy):
  persistence  — PERSISTENCE_WINDOWS consecutive alerting predictions open
                 an episode (one noisy score never triggers);
  hysteresis   — the episode stays active until risk falls below
                 LOW_FACTOR × threshold (not merely below threshold);
  cooldown     — after an episode clears, no new episode opens for
                 COOLDOWN_MINUTES of source time;
  dedup        — episode_key (first alerting window) + a unique constraint
                 give at most one unresolved alert per asset/policy episode.
"""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from ..models import AgentRun, Incident, ModelPrediction, ShadowAlert
from ..timeutil import utcnow
from .events import emit_event

PERSISTENCE_WINDOWS = 3
LOW_FACTOR = 0.8  # hysteresis close threshold = LOW_FACTOR * high threshold
COOLDOWN_MINUTES = 60


class ShadowTriggerError(Exception):
    pass


def _open_alert(
    session: Session, replay_run_id: int | None, asset_id: int, policy_version: str
) -> ShadowAlert | None:
    return session.scalars(
        select(ShadowAlert).where(
            ShadowAlert.replay_run_id == replay_run_id,
            ShadowAlert.asset_id == asset_id,
            ShadowAlert.policy_version == policy_version,
            ShadowAlert.hysteresis_state == "active",
        )
    ).first()


def _recent_consecutive_alerts(
    session: Session, prediction: ModelPrediction
) -> list[ModelPrediction]:
    """The unbroken run of alerting predictions ending at `prediction`."""
    rows = session.scalars(
        select(ModelPrediction)
        .where(
            ModelPrediction.replay_run_id == prediction.replay_run_id,
            ModelPrediction.asset_id == prediction.asset_id,
            ModelPrediction.model_source == prediction.model_source,
            ModelPrediction.window_end <= prediction.window_end,
        )
        .order_by(ModelPrediction.window_end.desc())
        .limit(PERSISTENCE_WINDOWS)
    ).all()
    streak = []
    for p in rows:
        if p.alert:
            streak.append(p)
        else:
            break
    return streak


def process_prediction(session: Session, prediction: ModelPrediction) -> ShadowAlert | None:
    """Feed one stored prediction through persistence/hysteresis/cooldown.
    Returns the open ShadowAlert if one exists after processing. NEVER
    creates an Incident or AgentRun — see module docstring."""
    alert = _open_alert(
        session, prediction.replay_run_id, prediction.asset_id, prediction.policy_version
    )
    low = prediction.threshold * LOW_FACTOR

    if alert is not None:
        if prediction.risk < low:
            alert.hysteresis_state = "cleared"
            alert.cooldown_until = prediction.window_end + timedelta(minutes=COOLDOWN_MINUTES)
            emit_event(
                session,
                "shadow_alert.cleared",
                {"shadow_alert_id": alert.id, "risk": prediction.risk},
            )
        elif prediction.alert:
            alert.latest_prediction_id = prediction.id
            alert.consecutive_windows += 1
        session.flush()
        return alert if alert.hysteresis_state == "active" else None

    if not prediction.alert:
        return None

    # Cooldown: the latest cleared episode blocks new ones for a while.
    last_cleared = session.scalars(
        select(ShadowAlert)
        .where(
            ShadowAlert.replay_run_id == prediction.replay_run_id,
            ShadowAlert.asset_id == prediction.asset_id,
            ShadowAlert.policy_version == prediction.policy_version,
            ShadowAlert.hysteresis_state == "cleared",
        )
        .order_by(ShadowAlert.id.desc())
    ).first()
    if (
        last_cleared
        and last_cleared.cooldown_until
        and prediction.window_end < last_cleared.cooldown_until
    ):
        return None

    streak = _recent_consecutive_alerts(session, prediction)
    if len(streak) < PERSISTENCE_WINDOWS:
        return None

    first = streak[-1]
    episode_key = f"{first.window_end.isoformat()}"
    existing = session.scalars(
        select(ShadowAlert).where(
            ShadowAlert.replay_run_id == prediction.replay_run_id,
            ShadowAlert.asset_id == prediction.asset_id,
            ShadowAlert.policy_version == prediction.policy_version,
            ShadowAlert.episode_key == episode_key,
        )
    ).first()
    if existing is not None:
        return existing

    alert = ShadowAlert(
        replay_run_id=prediction.replay_run_id,
        asset_id=prediction.asset_id,
        model_source=prediction.model_source,
        policy_mode=prediction.policy_mode,
        policy_version=prediction.policy_version,
        episode_key=episode_key,
        first_prediction_id=first.id,
        latest_prediction_id=prediction.id,
        high_threshold=prediction.threshold,
        low_threshold=low,
        consecutive_windows=len(streak),
        trigger_reason=(
            f"{len(streak)} consecutive windows with risk >= {prediction.threshold:.3f} "
            f"(first {first.window_end}, latest {prediction.window_end}); hysteresis "
            f"clears below {low:.3f}; WOULD-TRIGGER only — policy mode is "
            f"'{prediction.policy_mode}', no incident is opened automatically"
        ),
        status="observed",
    )
    session.add(alert)
    session.flush()
    emit_event(
        session,
        "shadow_alert.raised",
        {
            "shadow_alert_id": alert.id,
            "asset_id": alert.asset_id,
            "replay_run_id": alert.replay_run_id,
            "model_source": alert.model_source,
            "policy_mode": alert.policy_mode,
            "episode_key": episode_key,
            "consecutive_windows": alert.consecutive_windows,
        },
    )
    return alert


# ---------------------------------------------------------------- operator


def promote_shadow_alert(session: Session, alert_id: int, operator: str) -> Incident:
    """Explicit human transition: shadow alert → SYNTHETIC/DEMO incident.
    Idempotent AND race-safe: a compare-and-set claims the observed→promoted
    transition, so concurrent clicks produce exactly one incident — losers
    return the winner's incident. Creates NO agent run and approves nothing."""
    alert = session.get(ShadowAlert, alert_id)
    if alert is None:
        raise ShadowTriggerError(f"shadow alert {alert_id} not found")
    if alert.status == "promoted" and alert.incident_id is not None:
        return session.get(Incident, alert.incident_id)
    if alert.status == "dismissed":
        raise ShadowTriggerError(f"shadow alert {alert_id} was dismissed; cannot promote")
    if not operator:
        raise ShadowTriggerError("operator identity is required for promotion")

    # CAS: exactly one caller wins observed → promoted. A concurrent loser's
    # UPDATE blocks on the winner's row lock, returns rowcount 0 after the
    # winner commits, and then reads the winner's incident.
    claimed = session.execute(
        update(ShadowAlert)
        .where(ShadowAlert.id == alert_id, ShadowAlert.status == "observed")
        .values(status="promoted", decided_by=operator, decided_at=utcnow())
    ).rowcount
    if claimed != 1:
        session.rollback()
        session.expire_all()
        alert = session.get(ShadowAlert, alert_id)
        if alert.status == "promoted" and alert.incident_id is not None:
            return session.get(Incident, alert.incident_id)
        raise ShadowTriggerError(f"shadow alert {alert_id} is being promoted concurrently — retry")
    session.expire(alert)

    incident = Incident(
        asset_id=alert.asset_id,
        severity="warning",
        status="active",
        title=f"[SYNTHETIC/DEMO] Operator-promoted shadow alert #{alert.id} ({alert.model_source})",
        summary=(
            f"Synthetic demo incident created by operator '{operator}' from shadow alert "
            f"#{alert.id}. Provenance: model {alert.model_source}, policy mode "
            f"{alert.policy_mode} (version {alert.policy_version}), episode {alert.episode_key}, "
            f"{alert.consecutive_windows} consecutive alerting windows. The underlying "
            f"telemetry is real MetroPT-3 history; the incident itself is an operator-created "
            f"demo object, not an automatic model trigger."
        ),
        detected_at=utcnow(),
    )
    session.add(incident)
    session.flush()
    alert.incident_id = incident.id  # status/decided_* already set by the CAS
    emit_event(
        session,
        "shadow_alert.promoted",
        {
            "shadow_alert_id": alert.id,
            "incident_id": incident.id,
            "operator": operator,
        },
    )
    session.commit()
    return incident


def promote_and_investigate(session: Session, alert_id: int, operator: str):
    """Idempotent, crash-RECOVERABLE one-click operator flow: promote the
    shadow alert into its [SYNTHETIC/DEMO] incident AND start exactly one
    investigation, advanced through the read-only phases (Observe/Diagnose
    and a Plan PROPOSAL) to the human-approval gate. No write action executes
    here — approval remains a separate explicit human decision.

    SEMANTICS (deliberately not single-transaction "atomic"): the promotion
    commits first (CAS-claimed), then the run is created and advanced — the
    engine commits per phase by design. A crash or concurrent click between
    those commits CONVERGES on retry: promotion idempotency returns the
    existing incident, an existing run for the incident is reused, and the
    partial unique index on active runs makes the run insert exactly-once
    (a racing loser's IntegrityError/DuplicateRunError is caught and the
    winner's run returned). Verified with a real two-session PostgreSQL race
    test and a browser double-click. Returns (incident, run).
    """
    import time as _time

    from sqlalchemy import select as sa_select
    from sqlalchemy.exc import IntegrityError

    from .agent_loop import AgentEngine, DuplicateRunError

    incident = promote_shadow_alert(session, alert_id, operator)  # idempotent

    existing = session.scalars(
        sa_select(AgentRun).where(AgentRun.incident_id == incident.id).order_by(AgentRun.id)
    ).first()
    if existing is not None:
        return incident, existing

    alert = session.get(ShadowAlert, alert_id)
    try:
        engine = AgentEngine.start_run(
            session,
            incident.asset_id,
            trigger=f"operator '{operator}' promoted shadow alert #{alert.id} "
            f"({alert.model_source})",
            incident_id=incident.id,
            evidence_domain="metropt_public_replay",
        )
    except (DuplicateRunError, IntegrityError):
        # A concurrent click won the run-insert race (application guard or
        # the partial unique index). Reuse the winner's run once visible.
        session.rollback()
        for _ in range(20):
            existing = session.scalars(
                sa_select(AgentRun).where(AgentRun.incident_id == incident.id).order_by(AgentRun.id)
            ).first()
            if existing is not None:
                return incident, existing
            _time.sleep(0.05)
        raise ShadowTriggerError(
            f"concurrent investigation start for incident {incident.id} — retry"
        ) from None
    run = engine.run_until_blocked()  # stops at evidence/approval gate or escalates
    emit_event(
        session,
        "shadow_alert.investigated",
        {
            "shadow_alert_id": alert.id,
            "incident_id": incident.id,
            "run_id": run.id,
            "run_status": run.status,
            "operator": operator,
        },
    )
    session.commit()
    return incident, run


def dismiss_shadow_alert(session: Session, alert_id: int, operator: str) -> ShadowAlert:
    """Dismiss an alert. Creates nothing.

    Dismissal acknowledges the episode rather than ending it: while the score
    stays above the hysteresis threshold, further crossings are absorbed by
    this episode and don't re-alert. When the score clears, the normal
    cooldown applies and a later streak opens a new alert.
    """
    alert = session.get(ShadowAlert, alert_id)
    if alert is None:
        raise ShadowTriggerError(f"shadow alert {alert_id} not found")
    if alert.status == "promoted":
        raise ShadowTriggerError(f"shadow alert {alert_id} was already promoted")
    alert.status = "dismissed"
    alert.decided_by = operator
    alert.decided_at = utcnow()
    emit_event(
        session, "shadow_alert.dismissed", {"shadow_alert_id": alert.id, "operator": operator}
    )
    session.commit()
    return alert
