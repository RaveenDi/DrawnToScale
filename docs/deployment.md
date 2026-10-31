# Deployment

## Docker (recommended)

```bash
docker build -t drawn-to-scale .
docker run --rm -p 8000:8000 drawn-to-scale
```

Open http://127.0.0.1:8000. With no `MODEL_API_KEY` the app runs in **mock** mode (offline pipeline, canned model replies; never reported as model results).

Compose: `docker compose up --build`. To use Meta models, copy `.env.example` to `.env`, set `MODEL_PROVIDER=meta` and `MODEL_API_KEY`, then start with `docker compose --env-file .env up`.

## Environment

| Variable | Meaning |
|---|---|
| `MODEL_PROVIDER` | `mock` (default without a key), `meta` (live, records every call) or `replay` (answers from recordings, no network, no key) |
| `MODEL_API_KEY` | Meta Model API key. Never commit it. Server-side only. |
| `META_MODEL` | Model name, default `muse-spark-1.3` |
| `AGENT_MAX_STEPS` | Model turns in the matching loop (default 8) |
| `AGENT_MAX_MODEL_CALLS` | Model calls per run across attempts, incl. extraction and JSON repairs (default 16) |
| `AGENT_MAX_TOOL_CALLS` | Tool calls per matching loop (default 64) |

Other caps (config.py): 16 tool calls per turn, 2 consecutive unusable turns, 2 HTTP retries (backoff 1 s, 2 s; a `Retry-After` is honoured up to 30 s, a longer one is not waited out). Retried: HTTP 429/500/502/503/504 and timeouts/network errors. Never retried: other 4xx, redirects, truncated output. An invalid JSON/schema reply gets one repair request, then the run fails with a clear message.

## Record and replay

- `meta` mode saves every model request/response pair to `private/recordings/<sha256 of the normalized request>.json` (`private/` is gitignored). The inline image is stored as its SHA-256, the `Authorization` header and API key are never written, and only HTTP 200 responses are recorded.
- `replay` mode (`MODEL_PROVIDER=replay`, no key needed) answers the exact same request from those files and **fails loudly** (`Replay mode has no recording for this request`) on a miss, so a changed prompt or input can never silently fall back to the network. Geography is also offline in replay mode: it needs a cached Overpass snapshot in `private/geography-cache/` (created by one earlier `meta` run).
- Use it to re-run a paid session for free (tests, evals, demo rehearsal). Recordings are keyed on the exact request, so editing a prompt file invalidates the recordings that used it. Recordings can contain your sketch's text content: keep them private.

## Limits

- Run **one process** (no `--workers`): the worker is in-process and state is local.
- Data lives in `/app/private`; the compose file mounts a volume there. Add retention and auth before a public host.
- Live Meta endpoint shapes are **UNVERIFIED** until the real-model gate runs. The image has been built and started only where stated in the README status.


### Sketch geometry tolerance

`GEOMETRY_TOLERANCE_K` sets the nonnegative, finite scale factor (default `0.35`).
The tolerance is `max(50 m, k * median pairwise target-anchor distance)` and is
recorded in the verification verdict. Three-anchor reflection detection compares
signed triangle orientations; larger sets use the majority of nondegenerate
corresponding triangles so one outlier does not imply a whole-layout reflection.
`reflected` and `collinear` block fitting. `loose_fit` retains the transform and
residuals with `needs_review`; `too_few` requests more anchors. Every model
suggestion survives verification and still requires the person's confirmation.
Geometry consistency is not evidence that a proposed landmark is correct.
