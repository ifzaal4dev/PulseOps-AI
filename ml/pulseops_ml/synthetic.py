"""Constructed synthetic APU-telemetry generator (NOT derived from the real
dataset — safe for CI and for training disposable test artifacts).

Shared by ml/tests, apps/api tests (offline/online parity, inference tests),
and demos. Healthy pattern: the compressor cycles ~5 min loading / ~10 min
idle (COMP=0 while loading, matching the real data's convention). From day 3
12:00 an air-leak-like anomaly begins: duty roughly doubles, reservoir
pressure sags, motor current rises.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .dataset import ALL_COLUMNS

FIXTURE_START = pd.Timestamp("2021-03-01 00:00:00")  # deliberately outside the real 2020 range
CADENCE_S = 10
DAYS = 3
ANOMALY_START = pd.Timestamp("2021-03-03 12:00:00")
ANOMALY_END = pd.Timestamp("2021-03-03 18:00:00")

# Leakage-safe fixture training intervals (see ml/tests/test_train_eval.py).
TRAIN_START = "2021-03-01 00:00:00"
TRAIN_END = "2021-03-02 18:00:00"
VAL_END = "2021-03-03 00:00:00"


def synthetic_telemetry() -> pd.DataFrame:
    n = DAYS * 24 * 360  # 10 s cadence
    ts = pd.date_range(FIXTURE_START, periods=n, freq="10s")
    rng = np.random.default_rng(7)
    t = np.arange(n) * CADENCE_S

    anomalous = (ts >= ANOMALY_START) & (ts <= ANOMALY_END)
    cycle_s = np.where(anomalous, 450.0, 900.0)  # leak: full cycle shrinks to 7.5 min
    load_s = np.where(anomalous, 300.0, 300.0)  # 5 min loading either way
    phase = np.mod(t, cycle_s)
    loading = phase < load_s  # duty: healthy 1/3, anomalous 2/3

    tp2 = np.where(loading, 8.0 + rng.normal(0, 0.15, n), -0.01 + rng.normal(0, 0.01, n))
    reservoirs = 8.8 + 0.5 * np.sin(2 * np.pi * phase / cycle_s) + rng.normal(0, 0.05, n)
    reservoirs = np.where(anomalous, reservoirs - 0.9, reservoirs)
    tp3 = reservoirs + rng.normal(0, 0.03, n)
    h1 = np.where(loading, 0.25 + rng.normal(0, 0.02, n), -0.02 + rng.normal(0, 0.01, n))
    dv_pressure = np.where(loading, 0.1 + rng.normal(0, 0.02, n), -0.02 + rng.normal(0, 0.01, n))
    motor_current = np.where(loading, 6.0 + rng.normal(0, 0.2, n), 0.04 + rng.normal(0, 0.01, n))
    motor_current = np.where(anomalous & loading, motor_current + 1.5, motor_current)
    oil_temp = 55.0 + 4.0 * np.sin(2 * np.pi * t / 86400.0) + rng.normal(0, 0.3, n)
    oil_temp = np.where(anomalous, oil_temp + 6.0, oil_temp)

    comp = (~loading).astype(float)  # 0 while loading (real convention)
    dv_eletric = loading.astype(float)
    towers = np.ones(n)
    mpg = np.ones(n)
    lps = np.where(anomalous, (rng.random(n) < 0.2).astype(float), 0.0)
    pressure_switch = np.ones(n)
    oil_level = np.ones(n)
    caudal = loading.astype(float)

    df = pd.DataFrame(
        {
            "timestamp": ts,
            "TP2": tp2,
            "TP3": tp3,
            "H1": h1,
            "DV_pressure": dv_pressure,
            "Reservoirs": reservoirs,
            "Oil_temperature": oil_temp,
            "Motor_current": motor_current,
            "COMP": comp,
            "DV_eletric": dv_eletric,
            "Towers": towers,
            "MPG": mpg,
            "LPS": lps,
            "Pressure_switch": pressure_switch,
            "Oil_level": oil_level,
            "Caudal_impulses": caudal,
        }
    )
    assert list(df.columns) == ALL_COLUMNS
    return df
