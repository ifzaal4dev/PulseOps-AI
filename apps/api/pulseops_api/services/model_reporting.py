"""Read-only projection of the committed MetroPT holdout evaluation.

The UI must not invent or hand-copy performance claims. This loader reads the
machine-readable report produced by ``pulseops_ml.evaluate`` and exposes the
primary leakage-free policy, the same model under the app's trigger rules, and
the operational decision and caveats.
"""

from __future__ import annotations

import json
from pathlib import Path

DEFAULT_REPORT = Path(__file__).resolve().parents[4] / "ml" / "reports" / "metropt3_eval.json"


class ModelReportError(Exception):
    pass


def load_flagship_evaluation(path: Path | None = None) -> dict:
    report_path = path or DEFAULT_REPORT
    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ModelReportError(f"model evaluation report unavailable: {report_path}") from exc

    policies = {policy.get("policy"): policy for policy in report.get("policies", [])}
    primary = policies.get("iforest_global")
    decision = report.get("trigger_decision") or {}
    readiness = (decision.get("readiness_by_policy") or {}).get("iforest_global") or {}
    if primary is None or decision.get("model_source") != report.get("model_source"):
        raise ModelReportError("model evaluation report is missing primary-policy provenance")

    evaluated = report.get("evaluated_interval") or {}
    return {
        "generated_at": report["generated_at"],
        "task": report["task"],
        "model_source": report["model_source"],
        "feature_version": report["feature_version"],
        "evaluated_start_exclusive": evaluated.get("start_exclusive"),
        "evaluated_end": evaluated.get("end"),
        "holdout_windows": evaluated.get("n_holdout_windows", 0),
        "eligible_days": report.get("eligible_days", 0),
        "observed_days": report.get("observed_days"),
        "persistent_policy": policies.get("iforest_global_persistent"),
        "training_mode_windows": report.get("training_mode_windows", {}),
        "events_total": report.get("n_events", 0),
        "primary_policy": primary,
        "monthly_alert_rate": report.get("primary_model_monthly_alert_rate", {}),
        "false_alarm_episode_intervals": report.get(
            "primary_model_false_alarm_episode_intervals", []
        ),
        "monthly_mode_alert_rate": report.get("primary_model_monthly_mode_alert_rate", {}),
        "trigger_mode": decision.get("mode", "unknown"),
        "readiness_bar": decision.get("readiness_bar", {}),
        "readiness_blockers": readiness.get("blockers", []),
        "decision_rationale": decision.get("rationale", ""),
        "limitations": report.get("limitations", []),
    }
