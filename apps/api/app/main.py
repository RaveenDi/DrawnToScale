from contextlib import asynccontextmanager
import os
from pathlib import Path

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .contracts import Area, Confirmations, GraphEdit
from .exporting import export_geojson, review_report
from .mock import FixtureGeography, MockProvider
from .llm import Transport
from .providers import MetaProvider, OSMProvider, prepare_image
from .storage import Conflict, NotFound, Store
from .workflow import Workflow, apply_confirmations, apply_graph_edit

ROOT = Path(__file__).resolve().parents[3]
bearer = HTTPBearer(auto_error=False)


def create_app(data_dir=None, meta=None, geography=None):
    # Local project config only; no upward .env discovery or environment overrides.
    load_dotenv(ROOT / ".env", override=False)
    store = Store(data_dir or ROOT / "private")
    key = os.getenv("MODEL_API_KEY", "").strip()
    mode = os.getenv("MODEL_PROVIDER", "").strip().lower() or ("meta" if key else "mock")
    if mode not in {"mock", "meta", "replay"}:
        raise RuntimeError("MODEL_PROVIDER must be mock, meta or replay")
    recordings = store.root / "recordings"  # private/recordings: gitignored, never contains the API key
    model = os.getenv("META_MODEL", "muse-spark-1.3")
    if meta is None:
        if mode == "mock":
            meta, geography = MockProvider(), geography or FixtureGeography()
        elif mode == "meta":
            meta = MetaProvider(key, model, transport=Transport(key, recordings=recordings))
        else:
            meta = MetaProvider("", model, transport=Transport(mode="replay", recordings=recordings))
    geography = geography or OSMProvider(store.root / "geography-cache", offline=(mode == "replay"))
    workflow = Workflow(store, meta, geography)

    @asynccontextmanager
    async def lifespan(app):
        store.recover()
        yield
        workflow.close()
        for provider in (meta, geography):
            client = getattr(provider, "client", None)
            if client:
                client.close()

    app = FastAPI(title="Drawn to Scale — feasibility core", version="0.1.0", lifespan=lifespan,
                  description="Local-only review API. Real-model feasibility is UNVERIFIED. One worker; no automatic model retries.")
    app.state.store, app.state.workflow = store, workflow
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["localhost", "127.0.0.1", "testserver"])

    @app.middleware("http")
    async def local_request_guard(request, call_next):
        origin = request.headers.get("origin")
        if origin and origin not in {"http://127.0.0.1:8000", "http://localhost:8000", "http://127.0.0.1:5173", "http://localhost:5173", "http://testserver"}:
            return JSONResponse({"detail": "This prototype accepts local same-origin requests only"}, status_code=403)
        if request.method in {"POST", "PATCH"}:
            try:
                size = int(request.headers["content-length"])
            except (KeyError, ValueError):
                return JSONResponse({"detail": "A valid Content-Length is required"}, status_code=411)
            if size < 0 or size > 9 * 1024 * 1024:
                return JSONResponse({"detail": "Request exceeds the 9 MiB limit"}, status_code=413)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    @app.exception_handler(NotFound)
    async def not_found(request, error):
        return JSONResponse({"detail": "Run not found or access token is invalid"}, status_code=404)

    @app.exception_handler(Conflict)
    async def conflict(request, error):
        return JSONResponse({"detail": str(error)}, status_code=409)

    def authorized(run_id: str, credentials: HTTPAuthorizationCredentials | None = Depends(bearer)):
        if credentials is None or credentials.scheme.lower() != "bearer":
            raise HTTPException(401, "Use the run access token as a Bearer token")
        return store.get(run_id, credentials.credentials)

    @app.get("/api/health")
    def health():
        return {"status": "ok", "meta_configured": meta.configured, "provider": getattr(meta, "name", "custom"), "mode": "local_feasibility",
                "live_performance": "unverified"}

    @app.post("/api/runs", status_code=202)
    async def create_run(image: UploadFile = File(...),
                         area_json: str = Form(..., description='Bounding box JSON: {"west":79.84,"south":6.90,"east":79.86,"north":6.92}'),
                         context: str = Form("", max_length=2000)):
        if not meta.configured:
            raise HTTPException(503, "Set MODEL_API_KEY in project/.env and restart before a live run")
        try:
            area = Area.model_validate_json(area_json)
            raw = await image.read(8 * 1024 * 1024 + 1)
            normalized, info = prepare_image(raw)
        except (ValueError, ValidationError):
            raise HTTPException(422, "Use valid bounded area JSON and a PNG/JPEG up to 8 MiB and 20 megapixels") from None
        finally:
            await image.close()
        workflow.reserve()
        try:
            run, token = store.create(area, normalized, info, context)
        except Exception:
            workflow.slot.release()
            raise
        workflow.submit_reserved(run["id"])
        return {"id": run["id"], "access_token": token, "status": "created",
                "note": "Save the token locally; it is required to review, export and delete this run."}

    @app.get("/api/runs/{run_id}")
    def read_run(run=Depends(authorized)):
        return run

    @app.get("/api/runs/{run_id}/trace")
    def trace(run=Depends(authorized)):
        return {"id": run["id"], "status": run["status"], "model_calls": run["model_calls"], "steps": run.get("trace", [])}

    @app.get("/api/runs/{run_id}/image")
    def read_image(run=Depends(authorized)):
        return FileResponse(store.image_path(run["id"]), media_type="image/png")

    @app.patch("/api/runs/{run_id}/confirmations")
    def confirmations(request: Confirmations, run=Depends(authorized)):
        try:
            return apply_confirmations(store, run["id"], request)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None

    @app.patch("/api/runs/{run_id}/graph")
    def edit_graph(request: GraphEdit, run=Depends(authorized)):
        return apply_graph_edit(store, run["id"], request)

    @app.post("/api/runs/{run_id}/retry", status_code=202)
    def retry(run=Depends(authorized)):
        if run["status"] not in {"failed", "interrupted"} or run["attempts"] >= 3:
            raise Conflict("Only failed/interrupted runs with fewer than three attempts may retry")
        workflow.reserve()
        try:
            store.update(run["id"], lambda r: r.update(status="created", error=None),
                         revision=run["revision"], event={"kind": "user_retry"})
        except Exception:
            workflow.slot.release()
            raise
        workflow.submit_reserved(run["id"])
        return {"id": run["id"], "status": "created"}

    @app.get("/api/runs/{run_id}/export")
    def export(run=Depends(authorized)):
        return JSONResponse(export_geojson(run), media_type="application/geo+json",
                            headers={"Content-Disposition": f'attachment; filename="{run["id"]}.geojson"'})

    @app.get("/api/runs/{run_id}/report", response_class=PlainTextResponse)
    def report(run=Depends(authorized)):
        return review_report(run)

    @app.delete("/api/runs/{run_id}", status_code=204)
    def delete(run=Depends(authorized)):
        store.delete(run["id"])

    web_dist = ROOT / "apps/web/dist"
    if web_dist.is_dir():
        app.mount("/", StaticFiles(directory=web_dist, html=True), name="workspace")

    return app


app = create_app()
