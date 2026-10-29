import json

from fastapi.testclient import TestClient
import httpx
import pytest

from app.llm import ReplayMiss, Transport, request_key
from app.main import create_app
from app.providers import MetaProvider
from test_workflow import await_run, create_run

KEY = "sk-test-secret-123456"


def reply(graph):
    return httpx.Response(200, json={"model": "test-double", "id": "r1", "usage": {"prompt_tokens": 7, "completion_tokens": 3},
        "choices": [{"finish_reason": "stop", "message": {"content": graph.model_dump_json()}}]})


def live(tmp_path, graph, seen=None):
    def respond(request):
        if seen is not None:
            seen.append(request)
        return reply(graph)
    transport = Transport(KEY, client=httpx.Client(transport=httpx.MockTransport(respond)), recordings=tmp_path / "rec")
    return MetaProvider(KEY, transport=transport)


def replayer(tmp_path):
    return MetaProvider("", transport=Transport(mode="replay", recordings=tmp_path / "rec"))


def test_live_pairs_are_recorded_without_key_or_image_bytes(tmp_path, graph, image_bytes):
    live(tmp_path, graph).extract(image_bytes, "ctx")
    files = list((tmp_path / "rec").glob("*.json"))
    assert len(files) == 1 and len(files[0].stem) == 64
    text = files[0].read_text(encoding="utf-8")
    record = json.loads(text)
    assert KEY not in text and "Bearer" not in text and "authorization" not in text.lower()
    assert "base64," not in text and ";sha256:" in text  # image replaced by its digest
    assert record["request_sha256"] == files[0].stem and record["response"]["choices"]


def test_replay_returns_recorded_answer_with_no_network(tmp_path, graph, image_bytes):
    seen = []
    first, telemetry = live(tmp_path, graph, seen).extract(image_bytes, "ctx")
    assert len(seen) == 1
    provider = replayer(tmp_path)
    assert provider.configured and provider.client is None and provider.name == "replay"
    again, replay_telemetry = provider.extract(image_bytes, "ctx")
    assert again == first
    assert replay_telemetry["replayed"] is True and replay_telemetry["usage"] == telemetry["usage"]
    assert replay_telemetry["latency_ms"] == telemetry["latency_ms"]


def test_replay_miss_fails_loudly(tmp_path, graph, image_bytes):
    live(tmp_path, graph).extract(image_bytes, "ctx")
    with pytest.raises(ReplayMiss, match="no recording"):
        replayer(tmp_path).extract(image_bytes, "different context")
    with pytest.raises(ReplayMiss):
        replayer(tmp_path / "empty").extract(image_bytes, "ctx")


def test_request_key_is_stable_and_sensitive():
    a = {"model": "m", "messages": [{"role": "user", "content": "x"}]}
    assert request_key(a) == request_key(json.loads(json.dumps(a)))
    assert request_key(a) != request_key({**a, "model": "n"})
    image = {"image_url": {"url": "data:image/png;base64,AAAA"}}
    assert request_key(image) != request_key({"image_url": {"url": "data:image/png;base64,AAAB"}})


def test_replay_mode_app_reports_miss_as_failed_run(tmp_path, monkeypatch, image_bytes, area):
    monkeypatch.setenv("MODEL_PROVIDER", "replay")
    monkeypatch.setenv("MODEL_API_KEY", "")
    with TestClient(create_app(tmp_path)) as client:
        health = client.get("/api/health").json()
        assert health["provider"] == "replay" and health["meta_configured"] is True
        created = create_run(client, image_bytes, area)
        _, _, run = await_run(client, created)
        assert run["status"] == "failed" and "no recording" in run["error"]
