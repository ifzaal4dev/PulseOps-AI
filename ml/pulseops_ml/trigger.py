"""Offline replica of the app's shadow-trigger rules.

The app (apps/api/pulseops_api/services/shadow_trigger.py) doesn't alert on a
single threshold crossing. It opens an episode after PERSISTENCE consecutive
scored windows at or above the threshold, keeps it open until the score falls
below LOW_FACTOR × threshold (hysteresis), then blocks new episodes for
COOLDOWN_MINUTES. This module applies the same rules to a score series so the
offline evaluation can measure the policy the app actually runs.

A parity test in apps/api/tests feeds identical scores through both
implementations; keep the two in step.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import pandas as pd

PERSISTENCE = 3
LOW_FACTOR = 0.8
COOLDOWN_MINUTES = 60


@dataclass
class Episode:
    first_crossing: pd.Timestamp  # first window of the opening streak
    opened_at: pd.Timestamp  # window that completed the streak
    cleared_at: pd.Timestamp | None = None  # window whose score fell below the low threshold


def simulate_shadow_trigger(
    times: Sequence[pd.Timestamp],
    scores: Sequence[float],
    threshold: float,
    persistence: int = PERSISTENCE,
    low_factor: float = LOW_FACTOR,
    cooldown_minutes: int = COOLDOWN_MINUTES,
) -> tuple[list[Episode], list[bool]]:
    """Run the trigger over scored windows in time order.

    Returns the episodes and, for each window, whether an episode was open
    after processing it.
    """
    low = threshold * low_factor
    episodes: list[Episode] = []
    active_mask: list[bool] = []
    active: Episode | None = None
    cooldown_until: pd.Timestamp | None = None
    recent: list[bool] = []  # crossing flags of the last `persistence` windows

    for i, (t, score) in enumerate(zip(times, scores, strict=True)):
        crossed = score >= threshold
        recent = (recent + [crossed])[-persistence:]

        if active is not None:
            if score < low:
                active.cleared_at = t
                cooldown_until = t + pd.Timedelta(minutes=cooldown_minutes)
                active = None
        elif (
            crossed
            and (cooldown_until is None or t >= cooldown_until)
            and len(recent) == persistence
            and all(recent)
        ):
            active = Episode(first_crossing=times[i - persistence + 1], opened_at=t)
            episodes.append(active)

        active_mask.append(active is not None)

    return episodes, active_mask
