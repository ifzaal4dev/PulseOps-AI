# PulseOps task runner. On Windows, scripts/tasks.ps1 has the same targets.

PY ?= python
API_DIR = apps/api
WEB_DIR = apps/web

.PHONY: install install-ml install-api install-web reset api web test test-api test-ml eval lint format demo clean

install: install-ml install-api install-web

install-ml:
	$(PY) -m pip install -e "ml[dev]"

install-api:
	cd $(API_DIR) && $(PY) -m pip install -e ".[dev]"

install-web:
	cd $(WEB_DIR) && npm install

# Destructive: drop the database, migrate, and seed fresh demo data.
reset:
	cd $(API_DIR) && $(PY) -m pulseops_api.init_db --reseed

api:
	cd $(API_DIR) && $(PY) -m uvicorn pulseops_api.main:app --host 127.0.0.1 --port 8000 --reload

web:
	cd $(WEB_DIR) && npm run dev

test: test-api test-ml

test-api:
	cd $(API_DIR) && $(PY) -m pytest tests/ -q

test-ml:
	$(PY) -m pytest ml/tests -q

eval:
	$(PY) eval/run_eval.py

lint:
	$(PY) -m ruff check .
	$(PY) -m ruff format --check .
	cd $(WEB_DIR) && npm run lint && npm run format:check

format:
	$(PY) -m ruff format .
	cd $(WEB_DIR) && npm run format

# Headless demo of the synthetic Chiller-03 loop. Resets the configured database.
demo:
	cd $(API_DIR) && $(PY) -m pulseops_api.demo_golden_path

clean:
	rm -f $(API_DIR)/pulseops.db
