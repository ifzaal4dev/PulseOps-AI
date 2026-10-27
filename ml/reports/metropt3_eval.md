# MetroPT-3 Model — Holdout Evaluation Report

Generated: 2026-10-27T14:41:23.545963  ·  model: `metropt3_iforest_v1`  ·  features: `metropt3_fw_v1`

**Task: anomaly/failure detection (NOT demonstrated early warning — see lead-time sign convention)**

**Evaluated interval (holdout only)**: after 2020-04-16 00:00:00 → 2020-09-01 03:50:00 (16205 windows, 128.5 eligible days). all metrics computed only on windows ending strictly after the healthy-validation cutoff from artifact metadata; training and threshold-calibration intervals are excluded from every denominator.

Lead-time convention: positive hours = alert BEFORE documented onset (advance warning); negative = alert after onset (detection only).

## Trigger-policy comparison (thresholds from healthy data unless marked POST-HOC)

| Policy | Threshold | Recall | FA episodes | FA/day | Alert-window rate | Outside-event loaded rate | Outside-event idle rate |
|---|---|---|---|---|---|---|---|
| iforest_global | risk >= 0.491 | 3/4 | 23 | 0.179 | 0.0402 | 0.8036 | 0.0 |
| iforest_global_persistent | risk >= 0.491 for 3 consecutive windows; open until < 0.393; 60 min cooldown | 2/4 | 7 | 0.054 | 0.0513 | 0.8081 | 0.00014 |
| iforest_mode_conditioned | risk >= 0.533 (loaded) / 0.351 (idle) | 3/4 | 68 | 0.529 | 0.0463 | 0.012 | 0.04345 |
| zscore_healthy_calibrated | max|z| >= 4.363 | 1/4 | 3 | 0.023 | 0.0014 | 0.0075 | 0.0 |
| zscore_3_POST_HOC | max|z| >= 3.0 | 4/4 | 23 | 0.179 | 0.0731 | 0.9355 | 0.00014 |
| zscore_4_POST_HOC | max|z| >= 4.0 | 4/4 | 17 | 0.132 | 0.0555 | 0.6852 | 0.0 |

## Operational decision: **SHADOW**

No policy met the readiness bar with a leakage-free threshold: the IsolationForest misses one event and alerts on a large fraction of healthy loaded windows; the healthy-calibrated z-score detects only 1/4 events. The artifact is retained as an EXPERIMENTAL anomaly-detection model; the app must store predictions in SHADOW MODE and must not open incidents from them automatically.

Readiness bar: `{"min_event_recall": 1.0, "max_false_alarms_per_day": 0.25, "max_outside_event_loaded_alert_rate": 0.05}`

## Per-event detail (primary model: iforest_global)

| Event | Kind | Detected | First alert | Signed lead (h) |
|---|---|---|---|---|
| 2020-04-18 00:00:00 → 2020-04-18 23:59:00 | air_leak | True | 2020-04-18 00:30:00 | -0.5 |
| 2020-05-29 23:30:00 → 2020-05-30 06:00:00 | air_leak | False | None | None |
| 2020-06-05 10:00:00 → 2020-06-07 14:30:00 | air_leak | True | 2020-06-05 11:00:00 | -1.0 |
| 2020-07-15 14:30:00 → 2020-07-15 19:00:00 | air_leak | True | 2020-07-15 17:10:00 | -2.67 |

## Per-event detail (iforest_global_persistent: the app's trigger rules)

| Event | Detected | First open episode window | Signed lead (h) |
|---|---|---|---|
| 2020-04-18 00:00:00 → 2020-04-18 23:59:00 | False | None | None |
| 2020-05-29 23:30:00 → 2020-05-30 06:00:00 | False | None | None |
| 2020-06-05 10:00:00 → 2020-06-07 14:30:00 | True | 2020-06-05 11:20:00 | -1.33 |
| 2020-07-15 14:30:00 → 2020-07-15 19:00:00 | True | 2020-07-15 17:30:00 | -3.0 |

## Operating mode

Healthy training and calibration windows: 337 loaded, 8734 idle (3.7% loaded).
False-alarm episodes entirely in loaded windows: 23 of 23.
Outside-event alert rate: loaded 0.8036, idle 0.0.

| Month | Loaded windows | Loaded alert rate | Idle windows | Idle alert rate |
|---|---|---|---|---|
| 2020-04 | 163 | 0.0184 | 1460 | 0.0007 |
| 2020-05 | 262 | 0.5458 | 3303 | 0.0 |
| 2020-06 | 687 | 0.7045 | 2977 | 0.0 |
| 2020-07 | 70 | 0.2429 | 3629 | 0.0 |
| 2020-08 | 8 | 0.5 | 3640 | 0.0 |
| 2020-09 | 0 | None | 6 | 0.0 |

Most likely explanation: heavy load was rare in the healthy training data, so the model scores loaded operation as unusual. Air leaks make the compressor work harder, which may be part of why they are detected. This has not been confirmed experimentally (for example by training on loaded windows only).

## Monthly alert rate — primary model

```json
{
  "2020-04": 0.0025,
  "2020-05": 0.0401,
  "2020-06": 0.1321,
  "2020-07": 0.0046,
  "2020-08": 0.0011,
  "2020-09": 0.0
}
```

## Window-level (secondary)

precision 0.178 · recall 0.117 (windows are 10-min-step, 30-min-span and heavily autocorrelated; event-level metrics are the honest primary measure)

## Limitations

* only 4 independent failure episodes (all air leaks) — recall has huge variance; no claim generalizes beyond this unit/failure mode
* risk score is anomaly-based, not a calibrated failure probability
* all measured detections have NEGATIVE lead time (0.5–2.7h after onset): this is detection, not demonstrated advance warning
* alerting concentrates in loaded operation: false-alarm episodes fall in loaded windows and idle windows almost never alert, while heavy load was rare in the Feb–Apr healthy training data. This is the most likely explanation for the May–June rise; it has not been confirmed experimentally
* the offline metrics score single-window crossings unless the policy name says otherwise; iforest_global_persistent applies the app's trigger rules
* 24h lead-credit window and 12h post-event exclusion are reporting choices
* thresholds are selected only on healthy data; post-hoc z rows are illustration only
