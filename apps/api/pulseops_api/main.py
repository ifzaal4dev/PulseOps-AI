"""PulseOps API entry point.

Run locally:
    cd apps/api
    python -m pulseops_api.init_db          # migrate and seed an empty database
    python -m uvicorn pulseops_api.main:app --reload
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from . import __version__
from .config import settings
from .db import create_all
from .routes import agent, facility, replay

WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


@asynccontextmanager
async def lifespan(_app: FastAPI):
    create_all()
    yield


app = FastAPI(
    title="PulseOps AI",
    description="Closed-loop agentic reliability command center for smart facilities.",
    version=__version__,
    lifespan=lifespan,
)


async def require_json_for_writes(request: Request, call_next):
    """Reject writes that aren't sent as JSON.

    A browser can send a form or a bodyless POST to any origin without a CORS
    preflight. Requiring application/json forces the preflight, which other
    origins fail, so another website can't approve an action on a user's
    behalf.
    """
    if request.method in WRITE_METHODS:
        content_type = request.headers.get("content-type", "")
        if not content_type.lower().startswith("application/json"):
            return JSONResponse(
                {"detail": "write requests must use Content-Type: application/json"},
                status_code=415,
            )
    return await call_next(request)


# Registered before CORS so CORS stays the outermost layer and its headers are
# present on the 415 response too.
app.middleware("http")(require_json_for_writes)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.cors_origin],
    # Local development: the Next dev server may pick any localhost port.
    allow_origin_regex=r"http://(localhost|127\.0\.0\.1):\d+",
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(facility.router)
app.include_router(agent.router)
app.include_router(replay.router)


@app.get("/health")
def health():
    return {"status": "ok", "version": __version__}
