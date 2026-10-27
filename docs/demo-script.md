# Demo script: replay → shadow alert → investigation (about 5 minutes)

## Setup

```bash
# once: dataset and model (see ml/datasets/README.md for the download)
pip install -e "ml[dev]"
python -m pulseops_ml.ingest && python -m pulseops_ml.train

# the stack: PostgreSQL, API, and web
docker compose up --build

# reset to a clean seeded state (new terminal)
docker compose exec api python -m pulseops_api.init_db --reseed
```

The model is trained offline once; starting a replay never retrains it. Reset
between full runs of the demo. A replay whose alert was promoted can't be reset
on its own, because its incident and investigation are kept as an audit trail.

For a shorter demo that shows the system reusing what it learned, run the June
incident below once before presenting. The second run then finds that case in
memory.

## Walkthrough

1. **Command Center.** Two panels: "Real ML Demonstration — UCI MetroPT-3"
   for the real compressor, and "Synthetic Facility Operations Demo —
   mock_demo_v0" for the synthetic building. The guided-demo strip lists the
   steps.

2. **ML Model Lab** (optional). The holdout results come from the generated
   report. Point at the chronological chart (four documented failures, three
   detected, all after onset), the note that the app's own alert rule detects
   two of four with far fewer false alarms, and the month × operating-mode
   table (loaded windows alert, idle ones don't). That's why the model stays
   in shadow mode.

3. **Replay Lab.** The banner explains what's real (MetroPT-3 history, the
   trained model) and what's synthetic (the APU-01 asset record, other assets).

4. Click **⚠ June 5 air-leak event**, then **▶ Start**, then **Advance 1h
   source time** twice. The investigation timeline lines up the real sensor
   channels with the anomaly score on one time axis. It shows the 0.491
   threshold, the hysteresis line, crossing markers, the persistence strip, and
   the documented failure period. Hover for exact values, or click a window for
   its details: score, feature window, provenance, and the inputs that looked
   most unusual (descriptive, not causal). The score is an anomaly score, not a
   failure probability.

5. A **shadow alert** appears. The crossing streak starts at 11:00 and the
   alert opens at 11:20, after the third consecutive window. That matches the
   offline evaluation of the app's rule. No incident exists yet; the trigger
   state machine shows it waiting for a person.

6. Click **Promote & Investigate**. One `[SYNTHETIC/DEMO]` incident and one
   investigation are created (clicking twice doesn't duplicate them). Follow
   the **investigation run** link.

7. **Agent Control Room.** The run has stopped at the approval gate.
   * The reasoning timeline shows case memory was queried first. On a fresh
     database it finds no precedent; if the incident was run before, it cites
     that confirmed case.
   * The reasoning panel ranks compressed-air leak above an oil-system
     abnormality, with each piece of evidence linked to the tool call it came
     from. Synthetic manuals and notes are kept out of real-data
     investigations, so the evidence is sensor statistics and the model output.
   * The approval card shows the recommended action, its priority, and the
     exact MetroPT prediction and replay that triggered it. It also names the
     `mock_demo_v0` score used to check the simulated repair.
   * The tool audit trail labels every call trusted or untrusted.
   * The **Test configured LLM** panel is collapsed. **Show → Run test** sends
     one request through the real reasoning path against a synthetic snapshot
     and reports either "LIVE LLM RESPONSE ACCEPTED" or "LLM NOT USED —
     DETERMINISTIC FALLBACK". It creates nothing.

8. Click **✓ Approve**. The simulated environment responds and verification
   classifies the outcome as passed, failed, inconclusive, or adverse. Only
   passed closes the incident and saves a validated case to memory (visible in
   the ML Model Lab).

If the evidence had been tied, the run would have stopped at **Independent
evidence required** instead, with no action proposed, until a typed result
(for example an ultrasonic leak survey) was submitted.

## Headless version

`python -m pulseops_api.demo_golden_path` runs the synthetic Chiller-03
investigation in the terminal. No dataset is needed. It resets whatever
database `PULSEOPS_DATABASE_URL` points at.
