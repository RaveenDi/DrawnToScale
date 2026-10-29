# Agent architecture

Status: implemented and tested offline in mock mode. Live Meta behaviour is **UNVERIFIED**: no live call has been made, so there is no measured accuracy, latency or cost. Event/product facts: see the planning repo's `02-RULES.md`, `03-JUDGING-RUBRIC.md`, `05-META-PRODUCTS.md`.

## Hierarchy (one run)

```
Orchestrator (apps/api/app/workflow.py, deterministic, one worker, bounded)
 1. SketchReader   model   muse-spark-1.3 vision + structured output -> SketchGraph
 2. GeoRetriever   tool    OSM/Overpass (or offline snapshot) -> named candidates, label shortlist (<=6/landmark)
 3. Matcher        model   muse-spark-1.3 structured output -> allowed candidate ID or null per landmark
 4. Verifier       code    similarity fit (scale+rotation+shift, metres), outliers, leave-one-out error
      reject -> Matcher re-match of the outliers only (max 1 extra model call) -> Verifier again
 5. Human review   person  every landmark confirmed, changed or left unmatched; only then export
```

Every step is recorded as `{agent, role, model, input_summary, output_summary, duration_ms, status}` in the run and served by `GET /api/runs/{id}/trace`. The web UI shows it in the "Agent run" panel.

## Which Meta product, and why

| Agent | Meta product | Why |
|---|---|---|
| SketchReader | Meta Model API, `muse-spark-1.3` (image understanding + `json_schema` structured output) | One call turns a photo/scan of a sketch into a validated landmark graph; strict schema means no free text reaches the pipeline |
| Matcher | Meta Model API, `muse-spark-1.3` (structured output) | Judgement over names, types and relations in mixed languages/spelling; constrained to IDs it is given |
| GeoRetriever, Verifier | none | Geography and geometry must be reproducible and checkable, so they are plain code |

Request shapes follow the Meta docs linked in `README.md`; **compatibility with the live API is UNVERIFIED**. The open-weights models (Muse Glimmer / Spark Open) are not used yet; the provider interface (`extract`, `match`) is the seam where a local model would plug in (not built).

## Deterministic vs model boundary

- Models: read the image; choose among supplied candidates. Nothing else.
- Code: area bounds, image sanitising, OSM retrieval and caching, shortlist, ID validation, geometry, persistence, export, call budgets.
- Coordinates always come from stored OSM candidates. A model cannot emit a coordinate or a new ID (`validate_proposals` rejects it).

## Guardrails

- Strict Pydantic contracts (`extra=forbid`) on all model output; refusals, truncation and invalid JSON fail safely.
- Matcher sees only the sketch graph and allowed candidate IDs; never evaluator ground truth. Sketch text and candidate labels are treated as data, not instructions.
- Verifier rejection is bounded: one re-match, rejected IDs excluded, and anything still inconsistent becomes an abstention for the human.
- Hard caps: 1 extra re-match, 4 model calls and 3 attempts per run, 4096 completion tokens per call, no automatic retries, 8 MiB / 20 MP image, 12 landmarks, 20 km area, 1 worker.
- Nothing is exported unless the author confirmed it. Fit residuals are labelled "not independent accuracy".
- Secrets only in `.env`; mock mode needs none.

## Mock mode (what a judge runs)

`MODEL_PROVIDER=mock` (the default without `MODEL_API_KEY`) swaps in `MockProvider` (canned landmark layout, word-overlap matcher) and `FixtureGeography` (a published OSM snapshot of central Negombo, 259 named features). The first mock match is deliberately naive so the Verifier rejects one anchor and the bounded re-match path is exercised. The mock tests **plumbing only**; it is not evidence about Meta models. Mock mode ignores the uploaded image and only supports the Negombo area.

## How it is evaluated

- Offline tests (`pytest -q`): contracts, geometry, transport, persistence, mock end to end, trace shape and order.
- Planned, **NOT DONE**: the feasibility gate in `docs/04-FEASIBILITY-TEST.md` (five owned real sketches incl. held-out and ambiguous cases) with metrics recorded in `docs/05-TEST-RESULTS.md`; ablations (with/without Verifier, shortlist recall). No such results exist yet.

## Verified vs UNVERIFIED

- Verified locally: mock pipeline, trace, bounds, geometry on synthetic data, OSM retrieval of 259 named features for the Negombo test area (2026-10-29).
- UNVERIFIED: live `muse-spark-1.3` vision/structured-output behaviour, extraction and matching quality, shortlist recall on real names (e.g. a hand label "Railway Station" would miss an OSM feature just named "Negombo"), OSM coverage of the author's landmarks, usefulness to users.
