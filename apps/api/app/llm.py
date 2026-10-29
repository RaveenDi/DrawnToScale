"""Model transport: the only code that speaks HTTP to the model API.

Every live request/response pair is recorded (private/recordings/<sha256 of the normalized
request>.json), `replay` mode answers from those files and fails loudly on a miss. The API key
only ever lives in the Authorization header, which is never recorded."""
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import time

import httpx

META_URL = "https://api.meta.ai/v1/chat/completions"


class ProviderError(Exception):
    """An intentionally safe error message suitable for a run report."""


class ReplayMiss(ProviderError):
    """Replay mode has no recording for this exact request."""


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def timestamp():
    return datetime.now(timezone.utc).isoformat()


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: str  # raw JSON text exactly as the model produced it; parsed (and validated) by the agent loop


@dataclass
class AgentTurn:
    content: str | None
    tool_calls: list[ToolCall] = field(default_factory=list)
    telemetry: dict = field(default_factory=dict)


# Run-level model-call budget. The loop/workflow charges one call per logical request itself;
# transports charge only the *extra* calls they make internally (JSON repair).
_budget = ContextVar("model_call_budget", default=None)


@contextmanager
def call_budget(charge):
    token = _budget.set(charge)
    try:
        yield
    finally:
        _budget.reset(token)


def charge_extra_call():
    charge = _budget.get()
    if charge:
        charge()


def normalize_request(value):
    """Replace inline image payloads with their SHA-256 so recordings stay small and keys stay exact."""
    if isinstance(value, dict):
        return {k: normalize_request(v) for k, v in value.items()}
    if isinstance(value, list):
        return [normalize_request(v) for v in value]
    if isinstance(value, str) and value.startswith("data:image/") and ";base64," in value[:64]:
        head, payload = value.split(";base64,", 1)
        return f"{head};sha256:{digest(payload.encode())}"
    return value


def request_key(body):
    text = json.dumps(normalize_request(body), sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return digest(text.encode("utf-8"))


class Recordings:
    def __init__(self, directory):
        self.directory = Path(directory)

    def path(self, body):
        return self.directory / f"{request_key(body)}.json"

    def save(self, body, response, latency_ms):
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self.path(body)
        record = {"format": 1, "request_sha256": path.stem, "recorded_at": timestamp(),
                  "request": normalize_request(body), "response": response, "latency_ms": latency_ms}
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(record, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(temporary, path)

    def load(self, body):
        path = self.path(body)
        if not path.is_file():
            raise ReplayMiss(f"Replay mode has no recording for this request (sha256 {path.stem[:12]}). "
                             "It never calls the network; record it once with MODEL_PROVIDER=meta, or fix the changed prompt/input.")
        return json.loads(path.read_text(encoding="utf-8"))


def read_json_response(client, url, *, limit, **kwargs):
    started = time.monotonic()
    try:
        with client.stream("POST", url, **kwargs) as response:
            if response.status_code != 200:
                raise ProviderError(f"Provider returned HTTP {response.status_code}. No automatic retry.")
            chunks, size = [], 0
            for chunk in response.iter_bytes():
                size += len(chunk)
                if size > limit or time.monotonic() - started > 60:
                    raise ProviderError("Provider response exceeded the size or elapsed-time limit")
                chunks.append(chunk)
        return json.loads(b"".join(chunks))
    except (httpx.HTTPError, TimeoutError):
        raise ProviderError("Provider network/timeout failure. Remote outcome may be unknown; retry explicitly.") from None
    except (ValueError, UnicodeDecodeError):
        raise ProviderError("Provider returned invalid JSON") from None


class Transport:
    """POST one chat-completions body; returns (response_json, info)."""

    def __init__(self, key="", client=None, *, mode="live", recordings=None, url=META_URL):
        if mode not in {"live", "replay"}:
            raise ValueError("Transport mode must be live or replay")
        self.key, self.mode, self.url = key or "", mode, url
        self.recordings = Recordings(recordings) if recordings else None
        if mode == "replay" and not self.recordings:
            raise ValueError("Replay mode needs a recordings directory")
        self.client = None if mode == "replay" else (client or httpx.Client(
            timeout=httpx.Timeout(45, connect=10), follow_redirects=False))

    @property
    def configured(self):
        return self.mode == "replay" or bool(self.key.strip())

    def post(self, body):
        if self.mode == "replay":
            record = self.recordings.load(body)
            return record["response"], {"latency_ms": record.get("latency_ms"), "retries": 0, "replayed": True}
        if not self.configured:
            raise ProviderError("Set MODEL_API_KEY in project/.env before starting a live run")
        started = time.monotonic()
        response = read_json_response(self.client, self.url, limit=2 * 1024 * 1024,
                                      headers={"Authorization": "Bearer " + self.key}, json=body)
        latency_ms = round((time.monotonic() - started) * 1000)
        if self.recordings:
            self.recordings.save(body, response, latency_ms)
        return response, {"latency_ms": latency_ms, "retries": 0, "replayed": False}
