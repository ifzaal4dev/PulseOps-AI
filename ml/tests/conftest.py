"""Fixture wiring — the generator itself lives in pulseops_ml.synthetic so
apps/api tests and demos can share it."""

import pandas as pd
import pytest

from pulseops_ml.synthetic import (  # noqa: F401 — re-exported for tests
    ANOMALY_END,
    ANOMALY_START,
    FIXTURE_START,
    synthetic_telemetry,
)


@pytest.fixture(scope="session")
def telemetry() -> pd.DataFrame:
    return synthetic_telemetry()
