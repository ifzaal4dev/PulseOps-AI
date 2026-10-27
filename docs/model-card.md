# Model card: metropt3_iforest_v1

## Summary

| | |
|---|---|
| What it does | Flags unusual behaviour in an air compressor from its telemetry |
| What it doesn't do | Predict failures in advance, or give a probability of failure |
| Data | UCI MetroPT-3, real compressor telemetry from a metro train (CC BY 4.0) |
| Model | Isolation Forest on 60 windowed features |
| Status | Experimental. Runs in shadow mode: it records when it would have raised an alert, and a person decides |

The synthetic chillers and pumps in the app don't use this model. They use a
simple heuristic labelled `mock_demo_v0`. NASA C-MAPSS, which is simulated
turbofan data, isn't used anywhere. No company or customer data is involved.

## Data

* UCI MetroPT-3 (dataset 791): 1,516,948 rows at 10-second intervals, from
  2020-02-01 to 2020-09-01. Each row holds 7 analog and 8 digital channels
  from a metro train's air production unit.
* The download is checked against a pinned sha256 (`aab991a9…d8e1721a`).
* The dataset documents four failures, all air leaks. They're transcribed with
  their source in `ml/pulseops_ml/dataset.py` (`FAILURE_EVENTS`).

## Features

* 30-minute windows, one every 10 minutes.
* For each analog channel: mean, standard deviation, minimum, maximum, last
  value, and change over the window. For each digital channel: duty cycle and
  number of transitions. Plus the row count and data coverage. 60 features in
  total.
* Windows with less than half the expected data are skipped, not filled in.
* Features only look backwards. The same function (`FeatureComputer.at`)
  builds features for training and for the running app, and tests check the
  two agree exactly.

## Training

* StandardScaler and an Isolation Forest (300 trees, fixed seed) trained on
  healthy data only: 2020-02-01 to 2020-04-10 (8,390 windows). That ends 8
  days before the first documented failure.
* The alert threshold is the 99.5th percentile of scores on a separate healthy
  period, 2020-04-10 to 2020-04-16 (681 windows): **0.491**.
* Training stops with an error if either period overlaps a documented failure.
* The score is scaled so 0 is a typical healthy window and 0.5 is the 99.9th
  percentile of healthy windows. It's an anomaly score, not a probability.

## Evaluation

Everything below comes from `ml/reports/metropt3_eval.json`, produced by
`python -m pulseops_ml.evaluate`. It covers only data after 2020-04-16, which
neither the model nor the threshold saw: 16,205 windows.

A failure counts as detected if an alert happens between 24 hours before its
documented start and its documented end. False alarms are alerts outside those
periods (plus 12 hours after each), with alerts less than 30 minutes apart
merged into one episode.

Two denominators are reported for false alarms. "Eligible days" (128.5) is
the calendar span minus the failure periods. "Observed days" (104.2) counts
only the windows actually scored, because the data has gaps, including one of
about two days. The observed-day rate is the more accurate one.

### Results

| Policy | Failures detected | False-alarm episodes | Per eligible day | Per observed day |
|---|---|---|---|---|
| Isolation Forest, threshold 0.491, any single window | 3 of 4 | 23 | 0.179 | 0.221 |
| Same model and threshold, with the app's alert rule | 2 of 4 | 7 | 0.054 | 0.067 |
| Separate thresholds for loaded and idle operation | 3 of 4 | 68 | 0.529 | 0.653 |
| Simple z-score, threshold from healthy data (4.363) | 1 of 4 | 3 | 0.023 | 0.029 |
| z-score ≥ 3 (chosen after seeing the results) | 4 of 4 | 23 | 0.179 | 0.221 |
| z-score ≥ 4 (chosen after seeing the results) | 4 of 4 | 17 | 0.132 | 0.163 |

The app doesn't alert on a single window. It needs three consecutive windows
over the threshold, stays alerted until the score drops below 80% of the
threshold, and then waits 60 minutes before alerting again. The second row
scores exactly that rule (`pulseops_ml/trigger.py`, checked against the app by
a parity test). It removes most false alarms, but the April 18 detection
disappears, because that crossing never lasted three windows.

The last two rows look best, but their thresholds can only be picked by
looking at the failures. They're shown for comparison, not as options.

### Timing

Every detection came after the failure had started:

| Failure | Single-window policy | App's alert rule |
|---|---|---|
| 2020-04-18 | 0.5 h after onset | missed |
| 2020-05-29 | missed | missed |
| 2020-06-05 | 1.0 h after onset | 1.3 h after onset |
| 2020-07-15 | 2.7 h after onset | 3.0 h after onset |

So this is detection, not early warning. No advance warning has been shown.

### Where the false alarms come from

Every one of the 23 false-alarm episodes happened while the compressor was
loaded (working hard). Idle windows outside the failure periods never alerted.
Outside the failure periods, 80.4% of loaded windows alerted.

The training data explains this well. Of the 9,071 healthy windows used for
training and calibration, only 337 (3.7%) were loaded. The model learned that
heavy load is unusual, so it flags it. Air leaks make the compressor work
harder, which is probably part of why it catches them, and also why it raises
false alarms on busy but healthy days.

The share of alerting windows rises from 0.25% in April to 13.2% in June. An
earlier version of this card blamed seasonal drift. The month-by-mode split
points to operating mode instead: in June, 70% of loaded windows alerted and
0% of idle windows did. That's the most likely explanation, but it hasn't been
tested directly. The obvious next experiment is a model trained and calibrated
on loaded windows only.

Separate thresholds for loaded and idle operation cut loaded false alarms to
1.2%, but raised idle false alarms to 4.3% and total episodes to 68. The loaded
threshold also comes from only 337 windows, so it's a rough estimate.

## Decision: shadow mode

The readiness bar for automatic alerting was set before evaluation: detect
every documented failure, at most 0.25 false-alarm episodes per day, and at
most 5% of healthy loaded windows alerting. No policy with a threshold chosen
from healthy data meets it.

So the app stores every prediction with full provenance, records when it would
have alerted, and never opens an incident on its own. A person decides. The
decision and each policy's blockers are in
`ml/model_artifacts/trigger_policy.json`.

## Limitations

* Four failures, all air leaks. One more hit or miss moves recall by 25
  points, and nothing here says anything about other failure types or units.
* No demonstrated advance warning.
* False alarms are concentrated in loaded operation, most likely because load
  was rare in the training data.
* The 24-hour credit window before a failure and the 12-hour exclusion after
  it are reporting choices.
* The artifact records `sklearn_version=1.5.2`. The loader refuses to unpickle
  it under any other version, and the package pins that version.

## The mock adapter

`mock_demo_v0` (`apps/api/pulseops_api/services/failure_risk.py`) is a
bounded heuristic that scores the synthetic facility. Every response carries
its label and a disclaimer, and the UI shows it. It isn't trained and makes no
accuracy claims. It also provides the before/after score for simulated repair
verification, so that comparison always uses the same measure on both sides.

## Reproducing

```bash
pip install -e "ml[dev]"
# download the dataset first (ml/datasets/README.md)
python -m pulseops_ml.ingest
python -m pulseops_ml.train
python -m pulseops_ml.evaluate
python -m pytest ml/tests -q
```

`metadata.json`, `feature_spec.json` and `trigger_policy.json` are committed.
The model file itself (`.joblib`) isn't, so `train` regenerates it. Update the
numbers in this card only by re-running `evaluate`.
