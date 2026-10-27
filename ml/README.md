# PulseOps ML: MetroPT-3 anomaly detection

This package trains and evaluates the compressor anomaly detector used in the
app. It uses **UCI MetroPT-3**, real air production unit telemetry from a metro
train (CC BY 4.0, dataset 791). It never trains on the app's synthetic facility
data, and NASA C-MAPSS (simulated turbofan data) isn't used.

## Ground rules

1. Train only on real public data.
2. Raw data is downloaded separately and never committed. The pipeline checks
   the zip against a pinned sha256.
3. The model outputs an anomaly score from 0 to 1, not a failure probability.
   With four documented failures there isn't enough data for a calibrated
   probability.
4. In the app, this model scores only the replayed air compressor. The
   synthetic assets use the `mock_demo_v0` heuristic.

## Layout

```
ml/
├── pulseops_ml/
│   ├── dataset.py      # provenance, checksum, validation, parquet
│   ├── features.py     # windowed features; same code offline and online
│   ├── train.py        # healthy-period Isolation Forest + threshold
│   ├── evaluate.py     # chronological, event-based evaluation + reports
│   ├── trigger.py      # the app's alert rule, replayed offline
│   └── artifacts.py    # model / feature spec / metadata contract
├── tests/              # fixture-based tests (no dataset download needed)
├── datasets/           # download instructions; raw/ and processed/ are gitignored
├── model_artifacts/    # metadata.json, feature_spec.json, trigger_policy.json (model file gitignored)
└── reports/            # generated evaluation report
```

## Reproduce

```bash
pip install -e "ml[dev]"
# put the dataset in place first (datasets/README.md)
python -m pulseops_ml.ingest      # validate, write parquet and an ingest report
python -m pulseops_ml.train       # train on the healthy period, write artifacts
python -m pulseops_ml.evaluate    # evaluate on later data, write ml/reports/
python -m pytest ml/tests -q
```

## Results

Evaluated only on data after 2020-04-16, which the model and threshold never
saw (16,205 windows):

* Single-window alerts at the 0.491 threshold: **3 of 4** documented air
  leaks detected, 0.22 false-alarm episodes per observed day.
* With the app's alert rule (three consecutive windows, hysteresis, cooldown):
  **2 of 4** detected, 0.07 false-alarm episodes per observed day.
* Every detection came 0.5 to 3 hours **after** the leak started. This is
  detection, not early warning.
* All false alarms happened under heavy compressor load, which was rare in the
  healthy training data.

No policy met the readiness bar, so the app runs the model in **shadow mode**:
it records when it would have alerted and never opens incidents on its own.
Details: [`reports/metropt3_eval.md`](reports/metropt3_eval.md) and
[`docs/model-card.md`](../docs/model-card.md).
