"""UCI MetroPT-3 dataset: provenance, validation, and Parquet conversion.

Dataset: "MetroPT-3 Dataset" — UCI Machine Learning Repository id 791.
  https://archive.ics.uci.edu/dataset/791/metropt+3+dataset
  License: CC BY 4.0. REAL telemetry from a metro train's Air Production
  Unit (APU) compressor (Porto, Feb–Aug 2020), logged at 1 Hz on-board and
  published downsampled to 0.1 Hz (10 s cadence). 1,516,948 rows.
  Paper: Davari, Veloso, Ribeiro, Pereira, Gama (2021), "Predictive
  maintenance based on anomaly detection using deep learning for air
  production unit in the railway industry", IEEE DSAA 2021.

FAILURE_EVENTS below is transcribed from the failure-report table published
on the UCI dataset page (fetched 2026-07-11). All four reports are air-leak
failures. These are the ONLY independent failure episodes — evaluation must
be event-aware, never random row splits.

The raw zip stays out of git (ml/datasets/raw/ is gitignored); this module
verifies its checksum so results are tied to an exact dataset version.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path

import pandas as pd

ML_DIR = Path(__file__).resolve().parent.parent
RAW_DIR = ML_DIR / "datasets" / "raw" / "metropt3"
PROCESSED_DIR = ML_DIR / "datasets" / "processed"
CSV_NAME = "MetroPT3(AirCompressor).csv"
ZIP_NAME = "metropt3.zip"

# sha256 of the official UCI zip, computed at download (2026-07-11).
RAW_ZIP_SHA256 = "aab991a970e58210de853bb8078ce0e63abb4d9412fdc5c79792dae3d8e1721a"

# Column names exactly as they appear in the CSV header (including the
# dataset's own "DV_eletric" spelling).
ANALOG_COLUMNS = [
    "TP2",
    "TP3",
    "H1",
    "DV_pressure",
    "Reservoirs",
    "Oil_temperature",
    "Motor_current",
]
DIGITAL_COLUMNS = [
    "COMP",
    "DV_eletric",
    "Towers",
    "MPG",
    "LPS",
    "Pressure_switch",
    "Oil_level",
    "Caudal_impulses",
]
ALL_COLUMNS = ["timestamp"] + ANALOG_COLUMNS + DIGITAL_COLUMNS

# Plausible physical ranges (loose; violations are reported, not dropped).
# Idle readings sit slightly negative on pressure sensors (offset), so lower
# bounds allow that.
VALUE_RANGES = {
    "TP2": (-1.0, 15.0),  # bar
    "TP3": (-1.0, 15.0),  # bar
    "H1": (-1.0, 15.0),  # bar
    "DV_pressure": (-1.0, 15.0),  # bar
    "Reservoirs": (-1.0, 15.0),  # bar
    "Oil_temperature": (-10.0, 120.0),  # °C
    "Motor_current": (-1.0, 30.0),  # A
}

# Failure reports from the UCI page (all air leaks, "high stress").
FAILURE_EVENTS = [
    {"id": 1, "kind": "air_leak", "start": "2020-04-18 00:00:00", "end": "2020-04-18 23:59:00"},
    {"id": 2, "kind": "air_leak", "start": "2020-05-29 23:30:00", "end": "2020-05-30 06:00:00"},
    {"id": 3, "kind": "air_leak", "start": "2020-06-05 10:00:00", "end": "2020-06-07 14:30:00"},
    {"id": 4, "kind": "air_leak", "start": "2020-07-15 14:30:00", "end": "2020-07-15 19:00:00"},
]
FAILURE_EVENTS_SOURCE = (
    "UCI ML Repository dataset page id 791 (failure reports table), "
    "https://archive.ics.uci.edu/dataset/791/metropt+3+dataset, fetched 2026-07-11"
)


def failure_event_times() -> list[tuple[pd.Timestamp, pd.Timestamp, str]]:
    return [(pd.Timestamp(e["start"]), pd.Timestamp(e["end"]), e["kind"]) for e in FAILURE_EVENTS]


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


MISSING_DATA_MSG = f"""\
ERROR: MetroPT-3 raw data not found/invalid under {RAW_DIR}

The flagship model trains only on this real public dataset. To fix:
  1. Download the official zip (CC BY 4.0):
     https://archive.ics.uci.edu/static/public/791/metropt+3+dataset.zip
  2. Save it as {RAW_DIR / ZIP_NAME} and unzip so that
     {RAW_DIR / CSV_NAME} exists.
  3. Re-run. Expected zip sha256: {RAW_ZIP_SHA256}
"""


def verify_raw(strict_checksum: bool = True) -> Path:
    """Return the CSV path after presence + checksum validation."""
    csv_path = RAW_DIR / CSV_NAME
    zip_path = RAW_DIR / ZIP_NAME
    if not csv_path.exists():
        raise FileNotFoundError(MISSING_DATA_MSG)
    if zip_path.exists():
        digest = sha256_of(zip_path)
        if digest != RAW_ZIP_SHA256:
            msg = (
                f"MetroPT-3 zip checksum mismatch: got {digest}, expected {RAW_ZIP_SHA256}. "
                f"Dataset version differs from the one this pipeline was validated on."
            )
            if strict_checksum:
                raise ValueError(msg)
            print(f"WARNING: {msg}")
    elif strict_checksum:
        print(
            f"WARNING: {zip_path} not present — checksum not verifiable (CSV exists, continuing)."
        )
    return csv_path


def load_and_validate(csv_path: Path) -> tuple[pd.DataFrame, dict]:
    """Load the raw CSV, run integrity checks, return (clean df, report)."""
    df = pd.read_csv(csv_path, index_col=0)
    df, report = validate_frame(df)
    report["csv"] = str(csv_path)
    return df, report


def validate_frame(df: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """Integrity checks + gentle cleaning for a raw MetroPT-3 frame.

    Cleaning policy (all counted in the report, nothing silently dropped
    beyond what's listed): duplicate timestamps keep the first row;
    out-of-order rows are sorted; missing/out-of-range values are REPORTED
    and retained (window features are robust to a handful of oddities).
    """
    report: dict = {"rows_raw": len(df)}

    missing_cols = [c for c in ALL_COLUMNS if c not in df.columns]
    if missing_cols:
        raise ValueError(f"MetroPT-3 schema mismatch — missing columns: {missing_cols}")
    extra = [c for c in df.columns if c not in ALL_COLUMNS]
    report["unexpected_columns"] = extra

    if not pd.api.types.is_datetime64_any_dtype(df["timestamp"]):
        df["timestamp"] = pd.to_datetime(df["timestamp"], format="%Y-%m-%d %H:%M:%S")
    unsorted = int((df["timestamp"].diff() < pd.Timedelta(0)).sum())
    report["out_of_order_rows"] = unsorted
    if unsorted:
        df = df.sort_values("timestamp", kind="mergesort")

    dupes = int(df["timestamp"].duplicated().sum())
    report["duplicate_timestamps"] = dupes
    if dupes:
        df = df.drop_duplicates(subset="timestamp", keep="first")

    report["rows_clean"] = len(df)
    report["time_start"] = str(df["timestamp"].iloc[0])
    report["time_end"] = str(df["timestamp"].iloc[-1])

    deltas = df["timestamp"].diff().dt.total_seconds().dropna()
    report["median_cadence_seconds"] = float(deltas.median())
    report["gaps_over_60s"] = int((deltas > 60).sum())
    report["largest_gap_seconds"] = float(deltas.max())

    report["missing_values"] = {
        c: int(df[c].isna().sum()) for c in ALL_COLUMNS if df[c].isna().any()
    }

    out_of_range: dict[str, int] = {}
    for col, (lo, hi) in VALUE_RANGES.items():
        n = int(((df[col] < lo) | (df[col] > hi)).sum())
        if n:
            out_of_range[col] = n
    report["out_of_range_values"] = out_of_range

    non_binary: dict[str, int] = {}
    for col in DIGITAL_COLUMNS:
        n = int((~df[col].isin([0.0, 1.0])).sum())
        if n:
            non_binary[col] = n
    report["non_binary_digital_values"] = non_binary

    # Operating-mode analysis. Per the dataset paper, COMP is the air-intake
    # valve signal: COMP == 0 while the compressor is loading (producing air),
    # COMP == 1 while idle. Verified against the data: TP2 (compressor
    # pressure) is ~0 bar when COMP == 1 and elevated when COMP == 0.
    loaded = df["COMP"] == 0.0
    report["operating_modes"] = {
        "loaded_fraction": round(float(loaded.mean()), 4),
        "digital_duty_cycles": {c: round(float(df[c].mean()), 4) for c in DIGITAL_COLUMNS},
        "mean_TP2_loaded": round(float(df.loc[loaded, "TP2"].mean()), 3),
        "mean_TP2_idle": round(float(df.loc[~loaded, "TP2"].mean()), 3),
        "mean_motor_current_loaded": round(float(df.loc[loaded, "Motor_current"].mean()), 3),
        "mean_motor_current_idle": round(float(df.loc[~loaded, "Motor_current"].mean()), 3),
    }

    # Which documented failure events fall inside the data's actual range?
    t0, t1 = df["timestamp"].iloc[0], df["timestamp"].iloc[-1]
    report["failure_events_in_range"] = [
        e["id"]
        for e in FAILURE_EVENTS
        if pd.Timestamp(e["start"]) >= t0 and pd.Timestamp(e["end"]) <= t1
    ]

    df = df.reset_index(drop=True)
    return df, report


def to_parquet(df: pd.DataFrame, report: dict, out_dir: Path | None = None) -> Path:
    """Reproducible processed artifact + ingest report next to it."""
    out_dir = out_dir or PROCESSED_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    parquet_path = out_dir / "metropt3.parquet"
    df.to_parquet(parquet_path, index=False)
    report = dict(report)
    report["generated_at"] = datetime.now().isoformat()
    report["parquet_sha256"] = sha256_of(parquet_path)
    (out_dir / "metropt3_ingest_report.json").write_text(
        json.dumps(report, indent=2), encoding="utf-8"
    )
    return parquet_path


def processed_parquet_path() -> Path:
    """Resolve the processed-dataset location.

    Order: PULSEOPS_METROPT_PARQUET env var (containers/deployments — the
    package's own directory sits in site-packages there and never contains
    data), else the in-repo default next to this package.
    """
    import os

    env = os.environ.get("PULSEOPS_METROPT_PARQUET")
    return Path(env) if env else (PROCESSED_DIR / "metropt3.parquet")


def load_processed(path: Path | None = None) -> pd.DataFrame:
    path = path or processed_parquet_path()
    if path.is_dir():
        # Docker quirk: bind-mounting a MISSING host file silently creates a
        # directory at the target — surface that specifically.
        raise FileNotFoundError(
            f"{path} is a directory, not a Parquet file. If this is a container, "
            f"the host file behind the bind mount is missing — run "
            f"`python -m pulseops_ml.ingest` on the host so "
            f"ml/datasets/processed/metropt3.parquet exists, then recreate the "
            f"container."
        )
    if not path.exists():
        raise FileNotFoundError(
            f"{path} not found — run `python -m pulseops_ml.ingest` first (see "
            f"ml/README.md). In containers, set PULSEOPS_METROPT_PARQUET and "
            f"bind-mount the host's ml/datasets/processed/metropt3.parquet there "
            f"(docker-compose.yml does this)."
        )
    return pd.read_parquet(path)
