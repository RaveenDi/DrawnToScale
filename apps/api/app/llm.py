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


class TransientError(ProviderError):
    """HTTP 429/5xx or a timeout/network failure: worth a bounded retry."""

    def __init__(self, message, retry_after=None):
        super().__init__(message)
        self.retry_after = retry_after


TRANSIENT_STATUS = {429, 500, 502, 503, 504}


def parse_retry_after(value):
    """Retry-After in seconds; HTTP-date forms are ignored (the exponential backoff applies instead)."""
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        return None
    return seconds if 0 <= seconds < 86_400 else None


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
            if response.status_code in TRANSIENT_STATUS:
                raise TransientError(f"Provider returned HTTP {response.status_code}.", parse_retry_after(response.headers.get("retry-after")))
            if response.status_code != 200:
                raise ProviderError(f"Provider returned HTTP {response.status_code}. Not retried.")
            chunks, size = [], 0
            for chunk in response.iter_bytes():
                size += len(chunk)
                if size > limit or time.monotonic() - started > 60:
                    raise ProviderError("Provider response exceeded the size or elapsed-time limit")
                chunks.append(chunk)
        return json.loads(b"".join(chunks))
    except (httpx.TimeoutException, httpx.NetworkError, TimeoutError):
        raise TransientError("Provider network/timeout failure. Remote outcome may be unknown.") from None
    except httpx.HTTPError:
        raise ProviderError("Provider request failed. Not retried.") from None
    except (ValueError, UnicodeDecodeError):
        raise ProviderError("Provider returned invalid JSON") from None


class Transport:
    """POST one chat-completions body; returns (response_json, info)."""

    def __init__(self, key="", client=None, *, mode="live", recordings=None, url=META_URL,
                 retries=2, backoff_s=1.0, max_wait_s=30.0, sleep=time.sleep):
        if mode not in {"live", "replay"}:
            raise ValueError("Transport mode must be live or replay")
        self.key, self.mode, self.url = key or "", mode, url
        self.retries, self.backoff_s, self.max_wait_s, self.sleep = retries, backoff_s, max_wait_s, sleep
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
        for attempt in range(self.retries + 1):
            try:
                response = read_json_response(self.client, self.url, limit=2 * 1024 * 1024,
                                              headers={"Authorization": "Bearer " + self.key}, json=body)
                break
            except TransientError as exc:
                if attempt >= self.retries:
                    raise ProviderError(f"{exc} Gave up after {self.retries} retries.") from None
                if exc.retry_after is not None and exc.retry_after > self.max_wait_s:
                    raise ProviderError(f"{exc} Retry-After {exc.retry_after:g}s exceeds the {self.max_wait_s:g}s limit; not waiting.") from None
                self.sleep(max(self.backoff_s * 2 ** attempt, exc.retry_after or 0))  # honour Retry-After, else 1 s, 2 s
        latency_ms = round((time.monotonic() - started) * 1000)
        if self.recordings:
            self.recordings.save(body, response, latency_ms)
        return response, {"latency_ms": latency_ms, "retries": attempt, "replayed": False}
