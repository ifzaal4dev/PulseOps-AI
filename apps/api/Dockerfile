# PulseOps API — container image.
# BUILD CONTEXT IS THE REPO ROOT (see docker-compose.yml): the image installs
# both the API package and the pulseops-ml package (no code duplication) so
# online inference can import the shared feature/artifact code.
#
# The trained model binary stays out of the image; mount ml/model_artifacts
# and point PULSEOPS_ARTIFACTS_DIR at it (compose does this). With no artifact
# mounted, inference fails loudly (ModelSourceError) and everything else runs.
#
# Startup is NON-DESTRUCTIVE: init_db migrates to head and seeds only when the
# database is empty. Reset demo data explicitly:
#   docker compose exec api python -m pulseops_api.init_db --reseed
FROM python:3.12-slim

WORKDIR /app
COPY ml/pyproject.toml ml/pyproject.toml
COPY ml/pulseops_ml ml/pulseops_ml
COPY ml/reports/metropt3_eval.json ml/reports/metropt3_eval.json
RUN pip install --no-cache-dir ./ml

COPY apps/api/pyproject.toml apps/api/alembic.ini apps/api/
COPY apps/api/alembic apps/api/alembic
COPY apps/api/pulseops_api apps/api/pulseops_api
# [llm] bundles the optional Gemini/Anthropic SDKs so switching
# PULSEOPS_REASONING_MODE needs no rebuild; deterministic stays the default.
RUN pip install --no-cache-dir "./apps/api[llm]"

WORKDIR /app/apps/api
EXPOSE 8000
CMD ["sh", "-c", "python -m pulseops_api.init_db && uvicorn pulseops_api.main:app --host 0.0.0.0 --port 8000"]
