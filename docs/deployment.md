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

## Record and replay

- `meta` mode saves every model request/response pair to `private/recordings/<sha256 of the normalized request>.json` (`private/` is gitignored). The inline image is stored as its SHA-256, the `Authorization` header and API key are never written, and only HTTP 200 responses are recorded.
- `replay` mode (`MODEL_PROVIDER=replay`, no key needed) answers the exact same request from those files and **fails loudly** (`Replay mode has no recording for this request`) on a miss, so a changed prompt or input can never silently fall back to the network. Geography is also offline in replay mode: it needs a cached Overpass snapshot in `private/geography-cache/` (created by one earlier `meta` run).
- Use it to re-run a paid session for free (tests, evals, demo rehearsal). Recordings are keyed on the exact request, so editing a prompt file invalidates the recordings that used it. Recordings can contain your sketch's text content: keep them private.

## Limits

- Run **one process** (no `--workers`): the worker is in-process and state is local.
- Data lives in `/app/private`; the compose file mounts a volume there. Add retention and auth before a public host.
- Live Meta endpoint shapes are **UNVERIFIED** until the real-model gate runs. The image has been built and started only where stated in the README status.
