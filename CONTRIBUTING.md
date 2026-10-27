# Contributing

Thanks for taking a look. This file covers the design rules the code depends
on and how to run everything locally.

## Design rules

These hold the system together. Changes that break one of them need a very
good reason and a matching update to the docs and tests.

1. **No real company or customer data.** The facility (buildings, chillers,
   pumps, incidents, work orders) is synthetic.
2. **The flagship model is trained only on real data.** That's UCI MetroPT-3.
   Never train it on synthetic or simulated data. NASA C-MAPSS is simulated
   and isn't used. The synthetic assets use a heuristic labelled
   `mock_demo_v0`, and that label stays visible wherever its scores appear.
3. **Retrieved text is evidence, not instructions.** Manuals, notes and
   postmortems can influence a diagnosis only through the keyword whitelist in
   `services/evidence.py` and `services/knowledge.py`. Anything sent to an LLM
   goes in escaped evidence blocks.
4. **Write actions need human approval.** Every write follows
   propose → approve → execute (`services/approval.py` and the check in
   `execute_approved_action`). New write tools must use the same path.
5. **Diagnose checks case memory first.** Tests and the eval harness enforce
   this.
6. **Agent state lives in the database.** The phase state machine, the tool
   audit trail and the trust labels are contracts described in
   `docs/agentic-loop.md`, not implementation details.
7. **No hidden reasoning is stored.** `reasoning_summary` is a short audit
   line. Only validated structured output and a public rationale are kept.
8. **Performance numbers come from executed runs.** Model metrics are read
   from `ml/reports/metropt3_eval.json`, which `pulseops_ml.evaluate`
   generates. Don't hand-edit them.

## Setup

```bash
# backend (from apps/api)
pip install -e ".[dev]"
python -m pulseops_api.init_db          # migrate and seed (only seeds an empty DB)

# ML package (from the repo root)
pip install -e "ml[dev]"

# frontend (from apps/web)
npm install
```

The dataset isn't committed. See `ml/datasets/README.md` for the download, then
run `python -m pulseops_ml.ingest`, `train` and `evaluate`.

## Running things

```bash
# backend
python -m uvicorn pulseops_api.main:app --port 8000
python -m pulseops_api.init_db --reseed       # destructive: drop, migrate, reseed
python -m pulseops_api.demo_golden_path       # headless demo (resets the configured DB)

# frontend
npm run dev

# everything in containers (PostgreSQL by default)
docker compose up --build
```

## Checks

Run these before opening a pull request:

```bash
# from apps/api
python -m pytest tests/ -q
PULSEOPS_TEST_DATABASE_URL=postgresql+psycopg://user:pass@localhost:5433/db python -m pytest tests/ -q

# from the repo root
python -m pytest ml/tests -q
python eval/run_eval.py          # exits non-zero if any safety gate fails
ruff check . && ruff format --check .

# from apps/web
npm run lint && npm run build
```

`eval/run_eval.py` rewrites `eval/reports/`. Commit the updated reports when a
change is meant to affect agent behaviour.

On Windows, `scripts/tasks.ps1 <target>` mirrors the Makefile targets.
