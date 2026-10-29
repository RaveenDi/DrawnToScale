"""SQLite snapshots with optimistic revisions; private images live in a separate directory."""
from contextlib import contextmanager
import hashlib
import hmac
import json
from pathlib import Path
import secrets
import sqlite3
import uuid

from .providers import timestamp

ACTIVE = {"created", "extracting", "retrieving", "matching"}
EDITABLE = {"needs_confirmation", "ready"}


class Conflict(Exception):
    pass


class NotFound(Exception):
    pass


class Store:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.images = self.root / "images"
        self.images.mkdir(exist_ok=True)
        self.database = self.root / "runs.sqlite3"
        with self.connection() as db:
            db.execute("CREATE TABLE IF NOT EXISTS runs (id TEXT PRIMARY KEY, token_hash TEXT NOT NULL, data TEXT NOT NULL)")

    @contextmanager
    def connection(self):
        with sqlite3.connect(self.database, timeout=10) as db:
            yield db

    def create(self, area, image, image_info, context):
        run_id, token = str(uuid.uuid4()), secrets.token_urlsafe(32)
        run = {"id": run_id, "revision": 0, "status": "created", "created_at": timestamp(),
               "area": area.model_dump(), "image": image_info, "context": context,
               "original_graph": None, "graph": None, "candidates": [], "candidate_manifest": None,
               "shortlist": {}, "proposals": [], "confirmations": {}, "alignment": None,
               "model_calls": 0, "attempts": 0, "telemetry": [], "events": [], "error": None}
        self.image_path(run_id).write_bytes(image)
        try:
            with self.connection() as db:
                db.execute("INSERT INTO runs VALUES (?, ?, ?)",
                           (run_id, hashlib.sha256(token.encode()).hexdigest(), json.dumps(run)))
        except Exception:
            self.image_path(run_id).unlink(missing_ok=True)
            raise
        return run, token

    def image_path(self, run_id):
        # Canonical UUID validation prevents path traversal on image routes.
        try:
            if str(uuid.UUID(run_id)) != run_id:
                raise ValueError
        except (ValueError, TypeError, AttributeError):
            raise NotFound from None
        return self.images / f"{run_id}.png"

    def get(self, run_id, token=None):
        with self.connection() as db:
            row = db.execute("SELECT token_hash, data FROM runs WHERE id = ?", (run_id,)).fetchone()
        if not row or (token is not None and not hmac.compare_digest(row[0], hashlib.sha256(token.encode()).hexdigest())):
            raise NotFound
        return json.loads(row[1])

    def update(self, run_id, change, *, revision=None, event=None):
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT data FROM runs WHERE id = ?", (run_id,)).fetchone()
            if not row:
                raise NotFound
            run = json.loads(row[0])
            if revision is not None and revision != run["revision"]:
                raise Conflict("This run changed. Reload its current revision before editing.")
            change(run)
            run["revision"] += 1
            if event:
                run["events"].append({"at": timestamp(), "revision": run["revision"], **event})
            db.execute("UPDATE runs SET data = ? WHERE id = ?", (json.dumps(run), run_id))
        return run

    def recover(self):
        with self.connection() as db:
            rows = db.execute("SELECT id, data FROM runs").fetchall()
        for run_id, raw in rows:
            if json.loads(raw)["status"] in ACTIVE:
                self.update(run_id, lambda r: r.update(status="interrupted", error="Service restarted during processing. Retry explicitly."),
                            event={"kind": "interrupted"})

    def delete(self, run_id):
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT data FROM runs WHERE id = ?", (run_id,)).fetchone()
            if not row:
                raise NotFound
            if json.loads(row[0])["status"] in ACTIVE:
                raise Conflict("Wait for the active run to finish before deleting it")
            self.image_path(run_id).unlink(missing_ok=True)
            db.execute("DELETE FROM runs WHERE id = ?", (run_id,))
