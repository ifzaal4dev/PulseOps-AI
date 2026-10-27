"""Verification outcome classification.

Turns an observed (risk_before, risk_after, post-action telemetry volume)
triple into one of the four outcome semantics that control the agent's
workflow (see docs/agentic-loop.md):

  passed        risk meaningfully reduced AND now below the resolved ceiling
                → run may Learn, mitigate the incident, and write
                  validated-success memory.
  failed        enough telemetry, but risk not adequately reduced (includes
                partial recoveries that remain elevated)
                → incident stays open; failed attempt persisted as negative
                  memory; bounded re-diagnose/replan; any new action needs a
                  fresh human approval.
  inconclusive  not enough post-action telemetry to judge
                → run pauses; persists what it is waiting for; re-verification
                  on resume. No success claims, no mitigation.
  adverse       risk got meaningfully WORSE after the action
                → escalate to humans, raise incident severity, never
                  auto-execute another action.

All thresholds are documented policy, not magic numbers scattered in tools.
"""

from __future__ import annotations

# Risk must drop by at least this much to count as a real reduction.
MIN_REDUCTION = 0.10
# ...and end below this ceiling to call the incident resolved.
RESOLVED_RISK_MAX = 0.35
# Risk increasing by more than this margin is an adverse reaction.
ADVERSE_MARGIN = 0.05
# Minimum post-action readings (per sensor) required to judge at all.
MIN_POST_READINGS = 4


def classify_outcome(risk_before: float, risk_after: float, post_readings: int) -> tuple[str, str]:
    """Returns (outcome, reason). Deterministic; see module docstring."""
    if post_readings == 0:
        return "inconclusive", "no post-action telemetry observed yet"
    delta = risk_before - risk_after

    if delta <= -ADVERSE_MARGIN:
        return "adverse", (
            f"risk increased {risk_before:.2f} -> {risk_after:.2f} after the action "
            f"(margin {ADVERSE_MARGIN})"
        )
    if delta >= MIN_REDUCTION and risk_after <= RESOLVED_RISK_MAX:
        return "passed", (
            f"risk reduced {risk_before:.2f} -> {risk_after:.2f} "
            f"(>= {MIN_REDUCTION} reduction, below {RESOLVED_RISK_MAX} ceiling)"
        )
    if delta >= MIN_REDUCTION:
        return "failed", (
            f"partial recovery only: risk {risk_before:.2f} -> {risk_after:.2f} "
            f"remains above the {RESOLVED_RISK_MAX} resolved ceiling"
        )
    # No meaningful movement in either direction.
    if post_readings < MIN_POST_READINGS:
        return "inconclusive", (
            f"only {post_readings} post-action reading(s) per sensor "
            f"(need >= {MIN_POST_READINGS}) and no clear risk movement"
        )
    return "failed", (
        f"risk did not meaningfully improve: {risk_before:.2f} -> {risk_after:.2f} "
        f"(< {MIN_REDUCTION} reduction)"
    )
