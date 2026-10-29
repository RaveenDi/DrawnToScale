"""Fault injection: timeouts, HTTP 429/5xx, malformed output, caps. Nothing fails silently."""
import json

from fastapi.testclient import TestClient
import httpx
import pytest

from app.llm import ProviderError, Transport, call_budget
from app.main import create_app
from app.providers import MetaProvider
from conftest import FakeGeography
from test_workflow import await_run, create_run


def ok(graph, usage=None):
    return httpx.Response(200, json={"model": "m", "id": "x", "usage": usage or {"prompt_tokens": 10, "completion_tokens": 5},
                                     "choices": [{"finish_reason": "stop", "message": {"content": graph.model_dump_json()}}]})


def bad(text="{not json"):
    return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {"content": text}}]})


def provider(replies, sleeps, **kw):
    queue, calls = list(replies), []

    def respond(request):
        calls.append(json.loads(request.content))
        item = queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item
    transport = Transport("k", client=httpx.Client(transport=httpx.MockTransport(respond)), sleep=sleeps.append, **kw)
    return MetaProvider("k", transport=transport), calls


def timeout():
    return httpx.ReadTimeout("SECRET", request=httpx.Request("POST", "https://x"))


def test_timeouts_are_retried_with_exponential_backoff_then_succeed(graph, image_bytes):
    sleeps = []
    p, calls = provider([timeout(), timeout(), ok(graph)], sleeps)
    value, telemetry = p.extract(image_bytes, "")
    assert value == graph and len(calls) == 3 and sleeps == [1.0, 2.0] and telemetry["retries"] == 2


def test_429_retry_after_is_honoured_and_5xx_is_retried(graph, image_bytes):
    sleeps = []
    p, calls = provider([httpx.Response(429, headers={"retry-after": "7"}), httpx.Response(503), ok(graph)], sleeps)
    _, telemetry = p.extract(image_bytes, "")
    assert sleeps == [7.0, 2.0] and len(calls) == 3 and telemetry["retries"] == 2  # max(backoff, Retry-After)


def test_excessive_retry_after_is_not_waited_out(image_bytes):
    sleeps = []
    p, calls = provider([httpx.Response(429, headers={"retry-after": "600"})], sleeps)
    with pytest.raises(ProviderError, match="Retry-After 600s exceeds"):
        p.extract(image_bytes, "")
    assert sleeps == [] and len(calls) == 1


@pytest.mark.parametrize("status", [500, 502, 503, 504, 429])
def test_persistent_server_errors_stop_after_two_retries_with_clear_message(status, image_bytes):
    sleeps = []
    p, calls = provider([httpx.Response(status, text="SECRET BODY")] * 3, sleeps)
    with pytest.raises(ProviderError) as error:
        p.extract(image_bytes, "")
    assert f"HTTP {status}" in str(error.value) and "Gave up after 2 retries" in str(error.value) and "SECRET" not in str(error.value)
    assert len(calls) == 3 and sleeps == [1.0, 2.0]


@pytest.mark.parametrize("status", [400, 401, 403, 404, 302])
def test_client_errors_are_never_retried(status, image_bytes):
    sleeps = []
    p, calls = provider([httpx.Response(status)], sleeps)
    with pytest.raises(ProviderError):
        p.extract(image_bytes, "")
    assert len(calls) == 1 and sleeps == []


def test_invalid_json_gets_exactly_one_repair_then_succeeds_and_is_charged(graph, image_bytes):
    p, calls = provider([bad("{not json"), ok(graph)], [])
    charged = []
    with call_budget(lambda: charged.append(1)):
        value, telemetry = p.extract(image_bytes, "")
    assert value == graph and len(calls) == 2 and charged == [1]
    assert telemetry["repaired"] is True and telemetry["model_calls"] == 2
    repair = calls[1]["messages"][-2:]
    assert repair[0] == {"role": "assistant", "content": "{not json"} and "invalid" in repair[1]["content"]
    assert telemetry["usage"] == {"prompt_tokens": 10, "completion_tokens": 5}  # only the successful reply reported usage


def test_repair_usage_is_summed_when_both_replies_report_it(graph, image_bytes):
    wrong = httpx.Response(200, json={"usage": {"prompt_tokens": 7, "completion_tokens": 3},
                                      "choices": [{"finish_reason": "stop", "message": {"content": "{}"}}]})
    p, _ = provider([wrong, ok(graph)], [])
    assert p.extract(image_bytes, "")[1]["usage"] == {"completion_tokens": 8, "prompt_tokens": 17}


def test_second_invalid_reply_stops_with_clear_status_and_no_third_call(image_bytes):
    p, calls = provider([bad(), bad("{}")], [])
    with pytest.raises(ProviderError, match="still invalid after one repair attempt"):
        p.extract(image_bytes, "")
    assert len(calls) == 2


def test_truncated_output_is_not_repaired(image_bytes):
    p, calls = provider([httpx.Response(200, json={"choices": [{"finish_reason": "length", "message": {"content": "{"}}]})], [])
    with pytest.raises(ProviderError, match="incomplete"):
        p.extract(image_bytes, "")
    assert len(calls) == 1


def test_run_with_unrepairable_extraction_fails_loudly(tmp_path, image_bytes, area):
    p, calls = provider([bad(), bad()], [])
    with TestClient(create_app(tmp_path, p, FakeGeography([]))) as client:
        created = create_run(client, image_bytes, area)
        _, _, run = await_run(client, created)
    assert run["status"] == "failed" and "after one repair attempt" in run["error"] and run["graph"] is None
    assert run["model_calls"] == 2 and run["proposals"] == [] and len(calls) == 2


def test_repair_cannot_exceed_the_run_model_call_budget(tmp_path, monkeypatch, image_bytes, area):
    monkeypatch.setenv("AGENT_MAX_MODEL_CALLS", "1")
    p, calls = provider([bad(), bad()], [])
    with TestClient(create_app(tmp_path, p, FakeGeography([]))) as client:
        created = create_run(client, image_bytes, area)
        _, _, run = await_run(client, created)
    assert run["status"] == "failed" and "1-call model budget" in run["error"] and len(calls) == 1
