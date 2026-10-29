# Drawn to Scale

Turn an owned sketch of a known area into geographic landmarks that its author can inspect, correct and export.

**First implementation milestone: local API and processing core.** The full map interface is pending the real-sketch feasibility gate. Model performance is **UNVERIFIED**; no live model calls have been made. This folder has not been initialized as a Git repository or published.

## What works locally

- PNG/JPEG upload with image decoding, EXIF orientation correction and metadata removal. The image endpoint serves exactly the normalized image used for extraction.
- Versioned extraction and matching prompts, strict contracts, and bounded Meta transport. The intended model is `muse-spark-1.3`; request shape follows [Meta image understanding](https://dev.meta.ai/docs/image-understanding), [structured output](https://dev.meta.ai/docs/structured-output) and [chat completions](https://dev.meta.ai/docs/protocols/chat-completions). Live compatibility is unverified.
- Bounded OpenStreetMap candidate retrieval, a 24-hour cache, source IDs, timestamps and snapshot hashes. Candidate IDs are checked; a model cannot supply coordinates.
- Persistent SQLite runs, one processing slot, explicit retry, restart recovery, access tokens and optimistic edit revisions.
- User-confirmed correspondences, sketch corrections that invalidate old results, and local metre-based similarity fitting with outlier detection.
- GeoJSON containing only user-confirmed source points, plus unresolved items and provenance; a printable text review summary.

The current matcher shortlists up to six candidates per landmark by label similarity, then asks the model to select an allowed ID or abstain. The shortlist is a baseline, not a proven matching algorithm. Unknown names or missing OSM coverage can exclude the intended place; all retrieved candidates remain available for manual selection.

## Quick start (mock mode, no API key, no network)

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.lock.txt
.\scripts\start.ps1        # builds apps/web if needed, then serves http://127.0.0.1:8000
```

With no `MODEL_API_KEY` the app runs `MODEL_PROVIDER=mock`: a deterministic stand-in for the model plus an offline OSM snapshot of central Negombo. Open the page, choose **New sketch**, upload `evals/fixtures/demo-sketch.png` (synthetic, regenerate with `scripts/make_demo_sketch.py`), keep the Negombo area, and watch the **Agent run** panel: SketchReader, GeoRetriever, Matcher, Verifier (rejects one naive pick), bounded re-match, Verifier, human review. Then confirm the five landmarks and export GeoJSON. Mock mode ignores the image contents and tests plumbing only. See [docs/AGENT-ARCHITECTURE.md](docs/AGENT-ARCHITECTURE.md).

Live Meta mode (**UNVERIFIED**, billed): put `MODEL_API_KEY` in `.env` (never in chat or source) and set `MODEL_PROVIDER=meta` (or leave it unset; a key selects `meta`). Restart. API workbench: <http://127.0.0.1:8000/docs>. `GET /api/runs/{id}/trace` returns the agent steps. Run one server process and one worker; not for public hosting.

## First real case: Negombo

The user selected Negombo. [The initial area](evals/areas/negombo-central.json) is a provisional central area, not a claimed boundary for the city. Adjust it to the actual sketch. On 29 October 2026, a real read-only Overpass query returned **259 named features**; this proves retrieval only. The local snapshot and manifest are in `private/negombo-coverage.json`.

The area was centered using [the OSM Negombo place record](https://www.openstreetmap.org/node/2064194449). Data: [© OpenStreetMap contributors, ODbL](https://www.openstreetmap.org/copyright). Ways and relations use bounding-box centers, which are not necessarily entrances or suitable anchor locations. No candidate list has been labeled as evaluator ground truth.

```powershell
.\.venv\Scripts\python.exe scripts/check_geography.py
```

To run a real sketch through the API workbench:

1. Add the API key to `.env`, start the server, and open `/docs`.
2. Use `POST /api/runs` with your owned image, optional context without evaluator answers, and `area_json`:
   `{"west":79.823,"south":7.199,"east":79.848,"north":7.222}`.
3. Copy the returned run ID. Use the returned run access token in the workbench's **Authorize** control. It is separate from the Meta API key; keep it private.
4. Poll `GET /api/runs/{run_id}`. Inspect `/image`, extracted landmarks, candidates, source URLs and proposed IDs.
5. Use `PATCH /graph` to fix labels/positions when needed. This preserves the original extraction and clears all old confirmations and alignment. It makes no additional model call.
6. Use `PATCH /confirmations` with the current revision and decisions. Select any stored candidate ID, or `null` to explicitly leave a landmark unmatched:

```json
{
  "revision": 8,
  "decisions": [
    {"sketch_id": "p1", "candidate_id": "osm:node/REPLACE_WITH_ACTUAL_ID", "note": "Checked against the source map"},
    {"sketch_id": "p2", "candidate_id": null, "note": "Not mapped here"}
  ]
}
```

Use the IDs and revision actually returned by your run. A stale revision returns 409. Export `/export` and inspect `/report`. Partial exports include only confirmed points and retain the unresolved list. `ready` means all extracted landmarks have a review decision; it does not imply accurate alignment. At least three well-spread confirmed anchors are needed to attempt a fit.

## Verification

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

Current result: **36 tests passed**, plus a successful local service startup check of the health and OpenAPI routes. The server was shut down after that check; it is not currently left running.

The tests exercise synthetic geometry, mocked provider transport, errors, persistence, access checks, corrections, stale edits, export and deletion. They never contact Meta or OSM. In the Codex restricted Windows runner, the test client's internal loopback connection may require the normal host runner; this is separate from external network access. The pinned Starlette version currently emits one deprecation warning for its HTTPX test-client compatibility layer.

## Bounds and remaining work

- A normal nonempty run uses two model requests (three if the Verifier triggers its one bounded re-match), each capped at 4,096 completion tokens including reasoning. No automatic retry. Explicit retries reuse saved successful stages; each run permits at most three processing attempts and four model requests total. Provider timeout does not guarantee remote cancellation.
- Inputs: at most 8 MiB, 20 megapixels, one PNG/JPEG, 12 landmarks. Area: at most 20 km diagonal; no antimeridian crossing. More than 1,500 named geographic features requires a smaller area.
- Images, run records, reports and candidate caches stay under gitignored `private/`. Use the authenticated DELETE route to remove a run and normalized image. Automatic age-based retention, secure erasure and cloud-backup controls are not implemented; this directory may still be synced by OneDrive.
- The browser map, area search, manual pin placement, roads/boundaries, deployment and benchmark runner are not built. First milestone exports points only.
- Test five owned sketches, including held-out and ambiguous cases, before treating the product gate as passed. See the planning folder's feasibility protocol. Accuracy, time savings and real-model task performance remain unmeasured.

## Layout

`apps/api/app/` contains contracts, providers, prompts, geometry, persistence, workflow, export and routes. `apps/api/tests/` contains offline checks. `evals/areas/` contains public area configuration. `requirements.lock.txt` records the exact Windows/Python 3.12 environment tested. No frontend dependencies have been added yet.
