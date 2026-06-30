"""Aurea API entrypoint."""
from __future__ import annotations

import time
from collections import deque
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.api.router import api_router
from app.core.config import settings
from app.core.db import engine
from app.core.logging import configure_logging, get_logger

configure_logging()
log = get_logger("aurea.api")


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("api_startup", env=settings.app_env, llm_enabled=settings.llm_enabled)
    yield
    await engine.dispose()


app = FastAPI(
    title="Aurea — Wealth Intelligence Platform",
    description="Governed, agentic wealth management. Aurea Core · Agents · Atlas · Studio · "
                "Canvas · Provenance · Conduit.",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # local self-contained demo
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Lightweight per-IP rate limiting (in-memory sliding window) ───────────────
_RATE_WINDOW = 60.0
_RATE_LIMIT = 600  # requests / window / IP
_hits: dict[str, deque] = {}


@app.middleware("http")
async def rate_limit_and_headers(request: Request, call_next):
    path = request.url.path
    if path != "/health" and request.method != "OPTIONS":
        ip = request.client.host if request.client else "unknown"
        now = time.monotonic()
        dq = _hits.setdefault(ip, deque())
        while dq and now - dq[0] > _RATE_WINDOW:
            dq.popleft()
        if len(dq) >= _RATE_LIMIT:
            return JSONResponse(status_code=429, content={"detail": "Rate limit exceeded. Slow down."})
        dq.append(now)

    response = await call_next(request)
    # Security headers.
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["X-XSS-Protection"] = "0"
    response.headers["Permissions-Policy"] = "geolocation=(), microphone=(self), camera=()"
    return response


app.include_router(api_router)


@app.get("/health", tags=["meta"])
async def health():
    db_ok = True
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
    except Exception:
        db_ok = False
    return {
        "status": "ok" if db_ok else "degraded",
        "service": "aurea-api",
        "version": "1.0.0",
        "database": db_ok,
        "llm_enabled": settings.llm_enabled,
    }


@app.get("/", tags=["meta"])
async def root():
    return {
        "platform": "Aurea — The Wealth Intelligence Platform",
        "components": ["Aurea Core", "Aurea Agents", "Atlas", "Aurea Studio",
                       "Aurea Canvas", "Provenance", "Conduit"],
        "docs": "/docs",
    }
