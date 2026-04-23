"""
FastAPI application entry point.

Run with: uvicorn api.main:app --reload --port 8000
"""

import os
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from lambda_erp.database import setup

from api.errors import register_exception_handlers
from api.auth import router as auth_router, COOKIE_NAME, decode_token
from api.attachments import router as attachments_router
from api.chat import chat_websocket, router as chat_router
from api.routers import documents, masters, reports, setup as setup_router, bank_reconciliation, analytics


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: initialize database
    db_path = os.environ.get("LAMBDA_ERP_DB", "lambda_erp.db")
    setup(db_path)

    # When packaged as a demo container, land visitors straight in demo mode.
    if os.environ.get("LAMBDA_ERP_AUTO_DEMO") == "1":
        from api.bootstrap import bootstrap_demo

        bootstrap_demo()

    yield
    # Shutdown: nothing to clean up (SQLite handles it)


app = FastAPI(
    title="Lambda ERP",
    version="0.1.0",
    lifespan=lifespan,
)

# CORS for Vite dev server
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Exception handlers
register_exception_handlers(app)

# Routers
app.include_router(auth_router, prefix="/api")
app.include_router(attachments_router, prefix="/api")
app.include_router(documents.router, prefix="/api")
app.include_router(masters.router, prefix="/api")
app.include_router(reports.router, prefix="/api")
app.include_router(analytics.router, prefix="/api")
app.include_router(setup_router.router, prefix="/api")
app.include_router(bank_reconciliation.router, prefix="/api")
app.include_router(chat_router, prefix="/api")


@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.websocket("/ws/chat")
async def ws_chat(websocket: WebSocket):
    # Authenticate via cookie, fall back to public_manager for demo mode
    from lambda_erp.database import get_db

    token = websocket.cookies.get(COOKIE_NAME)
    user_name = decode_token(token) if token else None
    user = None

    db = get_db()
    if user_name:
        user = db.get_value("User", user_name, ["name", "full_name", "role", "enabled"])
        if user and not user.get("enabled"):
            user = None

    # Fall back to public manager
    if not user:
        pub = db.sql('SELECT name, full_name, role, enabled FROM "User" WHERE role = "public_manager" AND enabled = 1')
        user = pub[0] if pub else None

    if not user:
        await websocket.accept()
        await websocket.close(code=4001, reason="Not authenticated")
        return

    await chat_websocket(websocket, user_info=dict(user))


# Serve frontend build in production (if dist/ exists).
#
# In local dev the frontend runs on Vite's dev server (port 5173), which
# has SPA fallback built in — any unknown route serves index.html so
# React Router can take over. In the container, FastAPI serves the built
# frontend itself, and StaticFiles(html=True) only serves index.html for
# directory requests, not arbitrary paths. That made direct-URL visits
# and reloads on routes like /chat/:id return `{"detail": "Not Found"}`
# instead of the React app. The route below replicates Vite's fallback
# semantics: real files (assets, favicon, logos) resolve as-is; anything
# else falls through to index.html and React Router handles it
# client-side. API and WebSocket paths keep returning their normal 404s.
frontend_dist = os.path.join(os.path.dirname(__file__), "..", "frontend", "dist")
if os.path.isdir(frontend_dist):
    assets_dir = os.path.join(frontend_dist, "assets")
    if os.path.isdir(assets_dir):
        app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")

    frontend_root = os.path.realpath(frontend_dist)
    index_html = os.path.join(frontend_root, "index.html")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa_fallback(full_path: str):
        # Let API/WS paths that didn't match a real route 404 as JSON,
        # rather than accidentally returning the SPA shell HTML.
        if full_path.startswith(("api/", "ws/")):
            raise HTTPException(status_code=404, detail="Not Found")

        # Real public files (favicon.ico, favicon.png, logo_*.png, etc.)
        # should resolve to themselves. Guard against path traversal by
        # re-anchoring to the resolved frontend_dist root.
        if full_path:
            candidate = os.path.realpath(os.path.join(frontend_root, full_path))
            if candidate.startswith(frontend_root + os.sep) and os.path.isfile(candidate):
                return FileResponse(candidate)

        # SPA fallback — React Router takes over from here.
        return FileResponse(index_html)
