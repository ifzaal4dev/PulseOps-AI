"""Temporal, event-aware, HOLDOUT-ONLY evaluation of the MetroPT-3 model.

    python -m pulseops_ml.evaluate

Every published metric is computed exclusively on windows ending strictly
AFTER the healthy-validation cutoff stored in the artifact metadata
(`healthy_val_interval[1]`) — the model-training and threshold-calibration
intervals are excluded from all denominators. The evaluated interval is
reported explicitly.

The unit of evaluation is the failure EPISODE (four documented air-leak
reports); windows are never split randomly. Reported per policy:

  * event recall + missed events (alert within [start - 24h, end])
  * signed detection lead time per event (POSITIVE = alert before the
    documented onset = demonstrated advance warning; NEGATIVE = alert after
    onset = detection, not early warning)
  * false-alarm EPISODES per operating day (merged; event vicinity excluded)
  * alert-window rate (fraction of holdout windows alerting — episode
    merging must not hide alert volume)
  * outside-event alert rates split by operating mode (loaded vs idle)

Trigger-policy comparison: a global IsolationForest threshold, mode-
conditioned thresholds, and the trivial z-score baseline — each with
thresholds selected ONLY from healthy data (99.5th-percentile rule). Round-
number z thresholds are additionally shown but labeled post-hoc/illustrative:
they cannot be selected without peeking at events.

The evaluator writes ml/reports/metropt3_eval.{md,json} and
ml/model_artifacts/trigger_policy.json — the machine-readable operational
decision (automatic incident triggering vs shadow mode) that the app reads.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

from . import artifacts, dataset, features
from .train import BASELINE_FEATURES
from .trigger import COOLDOWN_MINUTES, LOW_FACTOR, PERSISTENCE, simulate_shadow_trigger

LEAD_HOURS = 24  # credit window before a reported start
POST_EXCLUDE_HOURS = 12  # windows shortly after an event don't count as false alarms
MERGE_GAP_MINUTES = 30  # consecutive alerts closer than this form one episode

# Operational-readiness bar for automatic incident triggering (documented policy):
READINESS = {
    "min_event_recall": 1.0,  # with 4 events, a miss is a 25% recall hit
    "max_false_alarms_per_day": 0.25,
    "max_outside_event_loaded_alert_rate": 0.05,  # >5% of healthy loaded windows alerting = alarm fatigue
}


def merge_episodes(times: list[pd.Timestamp], gap_minutes: int = MERGE_GAP_MINUTES) -> list[tuple]:
    if not times:
        return []
    times = sorted(times)
    episodes = [[times[0], times[0]]]
    for t in times[1:]:
        if (t - episodes[-1][1]).total_seconds() <= gap_minutes * 60:
            episodes[-1][1] = t
        else:
            episodes.append([t, t])
    return [tuple(e) for e in episodes]


def evaluate(
    data_path: Path | None,
    artifacts_dir: Path | None,
    events: list[dict] | None = None,
    reports_dir: Path | None = None,
) -> dict:
    pipeline, metadata, feature_spec = artifacts.load_artifacts(artifacts_dir)
    df = dataset.load_processed(data_path)
    events = events if events is not None else dataset.FAILURE_EVENTS
    event_times = [
        (pd.Timestamp(e["start"]), pd.Timestamp(e["end"]), e.get("kind", "failure")) for e in events
    ]

    # HOLDOUT BOUNDARY: strictly after the healthy-validation cutoff recorded
    # at training time. Single source of truth = artifact metadata.
    eval_start = pd.Timestamp(metadata["healthy_val_interval"][1])
    data_start = df["timestamp"].iloc[0]

    print(
        f"[evaluate] holdout evaluation strictly after {eval_start} "
        f"(train/threshold-calibration intervals excluded)"
    )
    X_all = features.build_feature_matrix(
        df,
        window_minutes=feature_spec["window_minutes"],
        step_minutes=feature_spec["step_minutes"],
    )
    assert list(X_all.columns) == feature_spec["feature_names"], "feature spec mismatch"
    X = X_all[X_all.index > eval_start]
    if X.empty:
        raise ValueError(f"no holdout windows after {eval_start}")

    def score(matrix: pd.DataFrame) -> pd.Series:
        return pd.Series(
            artifacts.risk_from_raw(
                -pipeline.decision_function(matrix.values), metadata["calibration"]
            ),
            index=matrix.index,
        )

    risk = score(X)
    stats = metadata["baseline_feature_stats"]

    def zscores(matrix: pd.DataFrame) -> pd.Series:
        return pd.concat(
            [((matrix[n] - stats[n]["mean"]) / stats[n]["std"]).abs() for n in BASELINE_FEATURES],
            axis=1,
        ).max(axis=1)

    z = zscores(X)
    # ALL thresholds are frozen at training time (artifact metadata); the
    # evaluator recomputes nothing from pre-cutoff data — the holdout-leakage
    # test depends on this.
    try:
        z_threshold = metadata["baseline_threshold_z"]
        mode_thr = metadata["mode_conditioned_thresholds"]
    except KeyError as e:
        raise ValueError(
            f"artifact metadata missing {e} — retrain with the current pipeline "
            f"(python -m pulseops_ml.train)"
        ) from e
    global_thr = metadata["threshold"]
    thr_loaded, thr_idle = mode_thr["loaded"], mode_thr["idle"]

    loaded = X["COMP_duty"] < 0.5
    exclusion = [
        (s - pd.Timedelta(hours=LEAD_HOURS), e + pd.Timedelta(hours=POST_EXCLUDE_HOURS))
        for s, e, _ in event_times
    ]
    in_exclusion = pd.Series(
        [any(lo <= t <= hi for lo, hi in exclusion) for t in X.index],
        index=X.index,
    )

    def event_metrics(alert_index: pd.DatetimeIndex) -> tuple[list[dict], int]:
        per_event, detected = [], 0
        for start, end, kind in event_times:
            span_alerts = alert_index[
                (alert_index >= start - pd.Timedelta(hours=LEAD_HOURS)) & (alert_index <= end)
            ]
            if len(span_alerts):
                detected += 1
                first = span_alerts.min()
                lead_h = (start - first).total_seconds() / 3600.0
                per_event.append(
                    {
                        "start": str(start),
                        "end": str(end),
                        "kind": kind,
                        "detected": True,
                        "first_alert": str(first),
                        "lead_time_hours": round(lead_h, 2),
                    }
                )
            else:
                per_event.append(
                    {
                        "start": str(start),
                        "end": str(end),
                        "kind": kind,
                        "detected": False,
                        "first_alert": None,
                        "lead_time_hours": None,
                    }
                )
        return per_event, detected

    total_days = (X.index.max() - X.index.min()).total_seconds() / 86400.0
    excluded_days = sum((hi - lo).total_seconds() for lo, hi in exclusion) / 86400.0
    eligible_days = max(total_days - excluded_days, 1e-9)
    # eligible_days spans the calendar, so data gaps count as observed time.
    # observed_days counts only the windows actually scored.
    step_minutes = feature_spec["step_minutes"]
    observed_days = max(float((~in_exclusion).sum()) * step_minutes / 1440.0, 1e-9)

    def false_alarm_metrics(alert_index: pd.DatetimeIndex) -> tuple[int, float]:
        outside = [t for t in alert_index if not any(lo <= t <= hi for lo, hi in exclusion)]
        episodes = merge_episodes(outside)
        return len(episodes), round(len(episodes) / eligible_days, 3)

    def policy_metrics(
        alert_mask: pd.Series, name: str, threshold_desc: str, selection: str
    ) -> dict:
        alert_index = X.index[alert_mask.values]
        per_event, detected = event_metrics(alert_index)
        episodes, fa_per_day = false_alarm_metrics(alert_index)
        out = ~in_exclusion
        return {
            "policy": name,
            "threshold": threshold_desc,
            "threshold_selection": selection,
            "event_recall": round(detected / len(event_times), 3) if event_times else None,
            "events_detected": detected,
            "events_missed": len(event_times) - detected,
            "per_event": per_event,
            "false_alarm_episodes": episodes,
            "false_alarms_per_operating_day": fa_per_day,
            "false_alarms_per_observed_day": round(episodes / observed_days, 3),
            "alert_window_rate": round(float(alert_mask.mean()), 4),
            "outside_event_loaded_alert_rate": round(float(alert_mask[loaded & out].mean()), 4)
            if int((loaded & out).sum())
            else None,
            "outside_event_idle_alert_rate": round(float(alert_mask[~loaded & out].mean()), 5)
            if int((~loaded & out).sum())
            else None,
        }

    healthy_rule = "99.5th percentile of healthy windows (no failure-event leakage)"
    policies = [
        policy_metrics(
            risk >= global_thr, "iforest_global", f"risk >= {global_thr:.3f}", healthy_rule
        ),
        policy_metrics(
            (risk >= np.where(loaded.values, thr_loaded, thr_idle)),
            "iforest_mode_conditioned",
            f"risk >= {thr_loaded:.3f} (loaded) / {thr_idle:.3f} (idle)",
            healthy_rule + f"; per-mode when >= {mode_thr['min_windows_per_mode']} healthy "
            f"windows (frozen at training time)",
        ),
        policy_metrics(
            z >= z_threshold,
            "zscore_healthy_calibrated",
            f"max|z| >= {z_threshold:.3f}",
            healthy_rule,
        ),
        policy_metrics(
            z >= 3.0,
            "zscore_3_POST_HOC",
            "max|z| >= 3.0",
            "POST-HOC round number — shown for illustration only; "
            "NOT selectable without failure-event leakage",
        ),
        policy_metrics(
            z >= 4.0,
            "zscore_4_POST_HOC",
            "max|z| >= 4.0",
            "POST-HOC round number — shown for illustration only; "
            "NOT selectable without failure-event leakage",
        ),
    ]

    # The app doesn't alert on single crossings: it applies persistence,
    # hysteresis, and a cooldown (trigger.py mirrors those rules). Score that
    # policy with the same frozen threshold. Because of hysteresis an open
    # episode can include windows below the threshold.
    assert X.index.is_monotonic_increasing
    _, persistent_active = simulate_shadow_trigger(list(X.index), list(risk.values), global_thr)
    policies.insert(
        1,
        policy_metrics(
            pd.Series(persistent_active, index=X.index),
            "iforest_global_persistent",
            f"risk >= {global_thr:.3f} for {PERSISTENCE} consecutive windows; "
            f"open until < {global_thr * LOW_FACTOR:.3f}; {COOLDOWN_MINUTES} min cooldown",
            healthy_rule + "; persistence, hysteresis, and cooldown as in the app",
        ),
    )

    # Operational-readiness check → automatic triggering vs shadow mode.
    def ready(p: dict) -> tuple[bool, list[str]]:
        reasons = []
        if p["threshold_selection"].startswith("POST-HOC"):
            reasons.append("threshold not selectable without event leakage")
        if p["event_recall"] is not None and p["event_recall"] < READINESS["min_event_recall"]:
            reasons.append(f"event recall {p['event_recall']} < {READINESS['min_event_recall']}")
        if p["false_alarms_per_operating_day"] > READINESS["max_false_alarms_per_day"]:
            reasons.append(
                f"false alarms/day {p['false_alarms_per_operating_day']} > "
                f"{READINESS['max_false_alarms_per_day']}"
            )
        rate = p["outside_event_loaded_alert_rate"]
        if rate is not None and rate > READINESS["max_outside_event_loaded_alert_rate"]:
            reasons.append(
                f"outside-event loaded alert rate {rate} > "
                f"{READINESS['max_outside_event_loaded_alert_rate']}"
            )
        return (not reasons), reasons

    readiness = {p["policy"]: {"ready": ready(p)[0], "blockers": ready(p)[1]} for p in policies}
    any_ready = [name for name, r in readiness.items() if r["ready"]]
    trigger_decision = {
        "generated_at": datetime.now().isoformat(),
        "model_source": metadata["model_source"],
        "mode": "auto_trigger" if any_ready else "shadow",
        "chosen_policy": any_ready[0] if any_ready else None,
        "readiness_bar": READINESS,
        "readiness_by_policy": readiness,
        "rationale": (
            f"policy '{any_ready[0]}' met the documented readiness bar on holdout data"
            if any_ready
            else "No policy met the readiness bar with a leakage-free threshold: the "
            "IsolationForest misses one event and alerts on a large fraction of "
            "healthy loaded windows; the healthy-calibrated z-score "
            "detects only 1/4 events. The artifact is retained as an EXPERIMENTAL "
            "anomaly-detection model; the app must store predictions in SHADOW "
            "MODE and must not open incidents from them automatically."
        ),
    }

    # Drift diagnosis for the primary model.
    alert_primary = pd.Series((risk >= global_thr).values, index=X.index)
    monthly = {
        str(p): round(float(g.mean()), 4)
        for p, g in alert_primary.groupby(alert_primary.index.to_period("M"))
    }

    # Chronological presentation data for the primary model. Purely additive:
    # the same frozen threshold and the same merged episodes the counts above
    # measure, now with their intervals so a UI can draw an honest timeline.
    outside_primary = [
        t for t in X.index[alert_primary.values] if not any(lo <= t <= hi for lo, hi in exclusion)
    ]
    fa_intervals = []
    for ep_start, ep_end in merge_episodes(outside_primary):
        span = (X.index >= ep_start) & (X.index <= ep_end) & alert_primary.values
        n_windows = int(span.sum())
        loaded_frac = float(loaded.values[span].mean()) if n_windows else None
        fa_intervals.append(
            {
                "start": str(ep_start),
                "end": str(ep_end),
                "n_alert_windows": n_windows,
                "loaded_fraction": round(loaded_frac, 3) if loaded_frac is not None else None,
            }
        )

    # Month × operating-mode alert rates: where and when the drift lives.
    monthly_mode: dict[str, dict] = {}
    periods = X.index.to_period("M")
    for period in sorted(set(periods)):
        row = {}
        for mode_name, mode_mask in (("loaded", loaded.values), ("idle", ~loaded.values)):
            sel = np.asarray(periods == period) & mode_mask
            n = int(sel.sum())
            row[mode_name] = {
                "windows": n,
                "alert_rate": round(float(alert_primary.values[sel].mean()), 4) if n else None,
            }
        monthly_mode[str(period)] = row

    mode_cal = metadata.get("mode_conditioned_thresholds", {})
    training_mode_windows = {
        "loaded": mode_cal.get("loaded_calibration_windows"),
        "idle": mode_cal.get("idle_calibration_windows"),
    }
    leads = [
        -e["lead_time_hours"] for e in policies[0]["per_event"] if e["lead_time_hours"] is not None
    ]

    # Window-level PR for the primary model (secondary; autocorrelation caveat).
    in_event = pd.Series(False, index=X.index)
    for start, end, _ in event_times:
        in_event |= (X.index >= start - pd.Timedelta(hours=LEAD_HOURS)) & (X.index <= end)
    tp = int((alert_primary & in_event).sum())
    fp = int((alert_primary & ~in_event).sum())
    fn = int((~alert_primary & in_event).sum())

    report = {
        "generated_at": datetime.now().isoformat(),
        "task": "anomaly/failure detection (NOT demonstrated early warning — see lead-time sign convention)",
        "model_source": metadata["model_source"],
        "feature_version": feature_spec["feature_version"],
        "evaluated_interval": {
            "start_exclusive": str(eval_start),
            "end": str(X.index.max()),
            "n_holdout_windows": int(len(X)),
            "note": (
                "all metrics computed only on windows ending strictly after the "
                "healthy-validation cutoff from artifact metadata; training and "
                "threshold-calibration intervals are excluded from every denominator"
            ),
            "raw_data_start": str(data_start),
        },
        "lead_time_convention": (
            "positive hours = alert BEFORE documented onset (advance warning); "
            "negative = alert after onset (detection only)"
        ),
        "eligible_days": round(eligible_days, 1),
        "observed_days": round(observed_days, 1),
        "training_mode_windows": training_mode_windows,
        "n_events": len(event_times),
        "policies": policies,
        "primary_model_monthly_alert_rate": monthly,
        "primary_model_false_alarm_episode_intervals": fa_intervals,
        "primary_model_monthly_mode_alert_rate": monthly_mode,
        "primary_model_window_pr": {
            "precision": round(tp / (tp + fp), 3) if tp + fp else None,
            "recall": round(tp / (tp + fn), 3) if tp + fn else None,
            "caveat": (
                "windows are 10-min-step, 30-min-span and heavily autocorrelated; "
                "event-level metrics are the honest primary measure"
            ),
        },
        "trigger_decision": trigger_decision,
        "limitations": [
            "only 4 independent failure episodes (all air leaks) — recall has huge variance; "
            "no claim generalizes beyond this unit/failure mode",
            "risk score is anomaly-based, not a calibrated failure probability",
            "all measured detections have NEGATIVE lead time "
            + (f"({min(leads):.1f}–{max(leads):.1f}h after onset)" if leads else "")
            + ": this is detection, not demonstrated advance warning",
            "alerting concentrates in loaded operation: false-alarm episodes fall in "
            "loaded windows and idle windows almost never alert, while heavy load was "
            "rare in the Feb–Apr healthy training data. This is the most likely "
            "explanation for the May–June rise; it has not been confirmed experimentally",
            "the offline metrics score single-window crossings unless the policy name "
            "says otherwise; iforest_global_persistent applies the app's trigger rules",
            "24h lead-credit window and 12h post-event exclusion are reporting choices",
            "thresholds are selected only on healthy data; post-hoc z rows are illustration only",
        ],
    }

    reports_dir = reports_dir or artifacts.REPORTS_DIR
    reports_dir.mkdir(parents=True, exist_ok=True)
    (reports_dir / "metropt3_eval.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    (reports_dir / "metropt3_eval.md").write_text(render_md(report), encoding="utf-8")

    policy_dir = artifacts_dir or artifacts.ARTIFACTS_DIR
    (policy_dir / "trigger_policy.json").write_text(
        json.dumps(trigger_decision, indent=2), encoding="utf-8"
    )

    print(f"[evaluate] holdout windows: {len(X)}  eligible days: {eligible_days:.1f}")
    for p in policies:
        print(
            f"  {p['policy']:28} recall {p['events_detected']}/{len(event_times)}  "
            f"FA/day {p['false_alarms_per_operating_day']}  "
            f"alert-window {p['alert_window_rate']}"
        )
    print(f"[evaluate] trigger decision: {trigger_decision['mode'].upper()}")
    print(f"[evaluate] reports written to {reports_dir}")
    return report


def render_md(r: dict) -> str:
    lines = [
        "# MetroPT-3 Model — Holdout Evaluation Report",
        "",
        f"Generated: {r['generated_at']}  ·  model: `{r['model_source']}`  ·  "
        f"features: `{r['feature_version']}`",
        "",
        f"**Task: {r['task']}**",
        "",
        f"**Evaluated interval (holdout only)**: after {r['evaluated_interval']['start_exclusive']} "
        f"→ {r['evaluated_interval']['end']} "
        f"({r['evaluated_interval']['n_holdout_windows']} windows, {r['eligible_days']} eligible days). "
        f"{r['evaluated_interval']['note']}.",
        "",
        f"Lead-time convention: {r['lead_time_convention']}.",
        "",
        "## Trigger-policy comparison (thresholds from healthy data unless marked POST-HOC)",
        "",
        "| Policy | Threshold | Recall | FA episodes | FA/day | Alert-window rate | "
        "Outside-event loaded rate | Outside-event idle rate |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for p in r["policies"]:
        lines.append(
            f"| {p['policy']} | {p['threshold']} | {p['events_detected']}/{r['n_events']} | "
            f"{p['false_alarm_episodes']} | {p['false_alarms_per_operating_day']} | "
            f"{p['alert_window_rate']} | {p['outside_event_loaded_alert_rate']} | "
            f"{p['outside_event_idle_alert_rate']} |"
        )
    d = r["trigger_decision"]
    lines += [
        "",
        f"## Operational decision: **{d['mode'].upper()}**",
        "",
        d["rationale"],
        "",
        f"Readiness bar: `{json.dumps(d['readiness_bar'])}`",
        "",
        "## Per-event detail (primary model: iforest_global)",
        "",
        "| Event | Kind | Detected | First alert | Signed lead (h) |",
        "|---|---|---|---|---|",
    ]
    for e in r["policies"][0]["per_event"]:
        lines.append(
            f"| {e['start']} → {e['end']} | {e['kind']} | {e['detected']} | "
            f"{e['first_alert']} | {e['lead_time_hours']} |"
        )
    lines += render_persistent_events(r)
    lines += render_operating_mode(r)
    pr = r["primary_model_window_pr"]
    lines += [
        "",
        "## Monthly alert rate — primary model",
        "",
        f"```json\n{json.dumps(r['primary_model_monthly_alert_rate'], indent=2)}\n```",
        "",
        "## Window-level (secondary)",
        "",
        f"precision {pr['precision']} · recall {pr['recall']} ({pr['caveat']})",
        "",
        "## Limitations",
        "",
    ]
    lines += [f"* {x}" for x in r["limitations"]]
    return "\n".join(lines) + "\n"


def render_persistent_events(r: dict) -> list[str]:
    policy = next((p for p in r["policies"] if p["policy"] == "iforest_global_persistent"), None)
    if policy is None:
        return []
    lines = [
        "",
        "## Per-event detail (iforest_global_persistent: the app's trigger rules)",
        "",
        "| Event | Detected | First open episode window | Signed lead (h) |",
        "|---|---|---|---|",
    ]
    for e in policy["per_event"]:
        lines.append(
            f"| {e['start']} → {e['end']} | {e['detected']} | "
            f"{e['first_alert']} | {e['lead_time_hours']} |"
        )
    return lines


def render_operating_mode(r: dict) -> list[str]:
    """Where the alerting happens, split by compressor load."""
    primary = r["policies"][0]
    episodes = r.get("primary_model_false_alarm_episode_intervals", [])
    all_loaded = sum(1 for e in episodes if e.get("loaded_fraction") == 1.0)
    train = r.get("training_mode_windows") or {}
    lines = ["", "## Operating mode", ""]
    if train.get("loaded") and train.get("idle"):
        total = train["loaded"] + train["idle"]
        lines.append(
            f"Healthy training and calibration windows: {train['loaded']} loaded, "
            f"{train['idle']} idle ({100 * train['loaded'] / total:.1f}% loaded)."
        )
    lines += [
        f"False-alarm episodes entirely in loaded windows: {all_loaded} of {len(episodes)}.",
        f"Outside-event alert rate: loaded {primary['outside_event_loaded_alert_rate']}, "
        f"idle {primary['outside_event_idle_alert_rate']}.",
        "",
        "| Month | Loaded windows | Loaded alert rate | Idle windows | Idle alert rate |",
        "|---|---|---|---|---|",
    ]
    for month, row in r.get("primary_model_monthly_mode_alert_rate", {}).items():
        lines.append(
            f"| {month} | {row['loaded']['windows']} | {row['loaded']['alert_rate']} | "
            f"{row['idle']['windows']} | {row['idle']['alert_rate']} |"
        )
    lines += [
        "",
        "Most likely explanation: heavy load was rare in the healthy training data, so "
        "the model scores loaded operation as unusual. Air leaks make the compressor "
        "work harder, which may be part of why they are detected. This has not been "
        "confirmed experimentally (for example by training on loaded windows only).",
    ]
    return lines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=None)
    parser.add_argument("--artifacts", type=Path, default=None)
    args = parser.parse_args(argv)
    evaluate(args.data, args.artifacts)
    return 0


if __name__ == "__main__":
    sys.exit(main())
