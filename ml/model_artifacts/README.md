# Model artifacts

Contents after `python -m pulseops_ml.train` (binaries gitignored, JSON committed):

```
model_artifacts/
├── metropt3_iforest_v1.joblib   # gitignored: StandardScaler + IsolationForest pipeline
├── metadata.json                # committed: dataset checksum, healthy intervals,
│                                #   calibration, threshold + method, code version, timings
├── feature_spec.json            # committed: feature version/window/step/names,
│                                #   the online-serving contract
└── trigger_policy.json          # committed: written by `evaluate`; shadow vs
                                 #   automatic alerting, with each policy's blockers
```

`metadata.json.model_source = "metropt3_iforest_v1"` is the provenance label
that appears on every prediction it makes. The app's synthetic assets keep
`mock_demo_v0`; the real model serves the MetroPT `air_compressor` asset only.

Evaluation reports live in `ml/reports/` (committed). Reproduce everything
with the commands in `ml/README.md`.
