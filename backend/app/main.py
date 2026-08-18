from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from .config import get_settings
from .db import initialize
from .services.display_language import (
    DISPLAY_LANGUAGE_HEADER,
    display_language_scope,
)


@asynccontextmanager
async def lifespan(_: FastAPI):
    settings = get_settings()
    initialize(settings.sqlite_path)
    from .seed import seed_defaults
    from .services.recovery import recover_stale_jobs
    seed_defaults(settings)
    recover_stale_jobs(settings, settings.job_stale_after_seconds)
    yield


app = FastAPI(title="DocSeek API", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def scope_display_language(request: Request, call_next):
    with display_language_scope(request.headers.get(DISPLAY_LANGUAGE_HEADER)):
        return await call_next(request)


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "docseek-api"}


def run() -> None:
    import uvicorn
    settings = get_settings()
    uvicorn.run("backend.app.main:app", host=settings.host, port=settings.api_port, reload=False)


from .api import auth, projects, status, properties, graphs, query, mcp, system, admin, profile  # noqa: E402

app.include_router(auth.router, prefix="/api")
app.include_router(projects.router, prefix="/api")
app.include_router(status.router, prefix="/api")
app.include_router(properties.router, prefix="/api")
app.include_router(graphs.router, prefix="/api")
app.include_router(query.router, prefix="/api")
app.include_router(mcp.router, prefix="/api")
app.include_router(mcp.transport_router, prefix="/api")
app.include_router(system.router, prefix="/api")
app.include_router(admin.router, prefix="/api")
app.include_router(profile.router, prefix="/api")
