# Agent architecture

Status: implemented and tested offline (mock provider, scripted tool-calling doubles, mocked HTTP). Live Meta behaviour is **UNVERIFIED**: no live call has been made, so there is no measured accuracy, latency or cost. Event/product facts: see the planning repo's `02-RULES.md`, `03-JUDGING-RUBRIC.md`, `05-META-PRODUCTS.md`.

## One run (plan, act, verify, recover)

```
Orchestrator (workflow.py: deterministic, one worker, hard caps)
 1. SketchReader  model  muse-spark-1.3 vision + json_schema      -> SketchGraph (1 JSON repair allowed)
 2. GeoRetriever  tool   OSM/Overpass or offline snapshot          -> named candidates + label shortlist
 3. Matcher       model  tool-calling loop (agent.py), <= 8 turns  -> one decision per landmark
      tools (tools.py, deterministic): search_candidates(query, kind?) · get_candidate(id) ·
      distance_between(a, b) · propose_match(sketch_id, candidate_id|null, evidence) · finish()
 4. Verifier      code   similarity fit (scale+rotation+shift, m), outliers, leave-one-out error
      reject -> feedback to the loop ONCE -> loop re-decides only the rejected landmarks -> Verifier again
 5. Human review  person every landmark confirmed, changed or left unmatched; only then export
```

Every model turn, tool call and Verifier pass is a trace step (`GET /api/runs/{id}/trace`, "Agent run" panel): `{agent, kind, role, model, input_summary, output_summary, duration_ms, status, telemetry?}`. Kinds: `model_call`, `model_turn`, `tool_call`, `deterministic`, `stop`, `human`.

## The matching loop

- The model sees the sketch graph and landmark IDs, **not** the candidate list: it must call `search_candidates`. Several tools may be called per turn.
- `propose_match` accepts only IDs that a search/get call returned in this run, never two landmarks on one candidate, and never an ID the Verifier already rejected for that landmark. Extra fields (such as a latitude) fail the strict argument contract. Coordinates exist only on stored candidates.
- Unknown tool, invalid JSON or invalid arguments return `{"ok": false, "error": ...}` to the model; nothing raises. `finish()` fails until every landmark has a decision (an ID or `null`).
- After `finish()` the deterministic Verifier decides. Outliers are dropped, their IDs excluded, and the model gets one feedback message. A second failure abstains those landmarks ("Geometry check failed twice").
- Caps (`config.py`, `AGENT_*` env): 8 model turns, 16 model calls per run (extraction, turns and repairs all count), 16 tool calls per turn, 64 per loop, 2 consecutive unusable turns. Ending on a cap returns a **partial result** and a visible status in `run.agent.status`: `partial_step_cap`, `partial_call_cap`, `stopped_provider_error`, `abstained_invalid_output`. Undecided landmarks abstain with a stated reason; a final Verifier pass checks whatever was decided. If the very first turn fails, the run fails (retryable) instead.
- Providers without `agent_turn` (test doubles) still use the original fixed Matcher + one re-match path.
- Mock mode drives the same loop with a scripted policy (search, get, propose+finish, then after feedback: search, distance, propose+finish). It exercises the plumbing, not model quality.

## Which Meta product, and why

| Agent | Meta product | Why |
|---|---|---|
| SketchReader | Meta Model API `muse-spark-1.3` (image input + `json_schema` structured output) | One call turns a sketch photo into a validated landmark graph; strict schema, no free text reaches the pipeline |
| Matcher | `muse-spark-1.3` with `tools`/`tool_calls` | The model decides what to look up, compares names/types/distances and when to abstain; the tools keep geography exact |
| GeoRetriever, Verifier, tools | none | Geography and geometry must be reproducible and checkable |

Open-weights models are not used yet. The provider seam is `extract`, `match` (legacy) and `agent_turn`.

## Telemetry, failure handling, replay

- Per step: model, prompt version (`extract-v1`, `match-agent-v1`), input/output tokens **when the API returns them**, latency, retries, repaired/replayed flags, estimated cost. Run totals are stored on the run and shown in the panel. Prices are placeholders in `config.py` marked **UNVERIFIED**; cost is labelled "estimated, UNVERIFIED prices" everywhere.
- Transport (`llm.py`): HTTP 429/500/502/503/504 and timeouts get at most 2 retries (1 s, 2 s backoff; `Retry-After` honoured up to 30 s, longer is refused); other 4xx, redirects and truncated output are never retried. A reply that is not valid JSON/schema gets one repair request (counted as a model call), then the run fails with "still invalid after one repair attempt". Fault-injection tests cover each case.
- `MODEL_PROVIDER=meta` records every request/response pair to `private/recordings/<sha256 of the normalized request>.json` (image stored as a digest, no key). `replay` answers from them offline and raises on a miss. Details: `docs/deployment.md`.

## Deterministic vs model boundary and guardrails

- Models: read the image; choose tools and candidates. Code: bounds, image sanitising, OSM retrieval/cache, search, ID validation, geometry, persistence, export, budgets.
- Strict Pydantic contracts (`extra=forbid`) on model output; sketch text and candidate labels are data, not instructions. Evaluator ground truth never reaches a model (test inspects outgoing requests).
- Nothing is exported unless the author confirmed it. Fit residuals are labelled "not independent accuracy".
- Secrets only in `.env`; mock and replay need none. Inputs: 8 MiB / 20 MP image, 12 landmarks, 20 km area, 3 attempts per run, 1 worker.

## How it is evaluated

- `pytest -q`: contracts, geometry, transport, retries/repair, loop caps, recovery, tool errors, telemetry, replay, scoring, harness.
- `evals/run.py --provider mock|replay|meta --split dev|heldout` scores extraction recall/precision, match accuracy, abstain correctness, geographic error (m), steps, calls, tokens, latency and cost against ground truth written beforehand ([evals/README.md](../../evals/README.md)). Held-out is reported once.
- **NOT DONE:** the real-sketch gate (`docs/planning/04-FEASIBILITY-TEST.md`), any live run, ablations (tool loop vs fixed Matcher, with/without Verifier).

## Verified vs UNVERIFIED

- Verified locally (2026-10-29): mock pipeline and trace, caps and recovery with scripted policies, HTTP fault handling against mocked transports, record/replay round trip, harness on the synthetic demo manifest, geometry on synthetic data, OSM retrieval of 259 features for the Negombo test area.
- UNVERIFIED: the live `tools`/`tool_calls` wire shape and `usage` field names (isolated in `MetaProvider.wire_*`/`agent_turn`), live `muse-spark-1.3` extraction/matching quality and tool-use behaviour, real latency and prices, shortlist recall on real names, OSM coverage of the author's landmarks, usefulness to users.
