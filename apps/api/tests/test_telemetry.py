from fastapi.testclient import TestClient
import pytest

from app.main import create_app
from app.telemetry import cost_basis, estimate_cost, normalize, token_counts, totals
from test_agent import Scripted, decide, searches
from test_workflow import await_run, create_run


def test_token_counts_accept_both_shapes_and_missing_usage():
    assert token_counts({"prompt_tokens": 5, "completion_tokens": 2}) == (5, 2)
    assert token_counts({"input_tokens": 7, "output_tokens": 1}) == (7, 1)
    assert token_counts({"total_tokens": 9}) == (None, None) and token_counts(None) == (None, None)
    assert token_counts({"prompt_tokens": True}) == (None, None)


def test_cost_is_estimated_only_with_tokens_and_a_priced_model_and_labelled_unverified():
    assert estimate_cost("muse-spark-1.3", 1_000_000, 1_000_000) == 5.0  # placeholder 1.0 + 4.0 USD per million tokens
    assert estimate_cost("muse-spark-1.3", None, 5) is None and estimate_cost("unknown-model", 1, 1) is None
    assert "UNVERIFIED" in cost_basis("muse-spark-1.3") and cost_basis("unknown-model") is None
    step = normalize({"usage": {"prompt_tokens": 100, "completion_tokens": 50}, "latency_ms": 321, "requested_model": "muse-spark-1.3",
                      "prompt_version": "extract-v1", "retries": 1}, model="x", prompt_version="p", duration_ms=999)
    assert (step["input_tokens"], step["output_tokens"], step["latency_ms"], step["retries"]) == (100, 50, 321, 1)
    assert step["prompt_version"] == "extract-v1" and step["est_cost_usd"] == 0.0003 and "UNVERIFIED" in step["cost_basis"]
    bare = normalize({"provider": "mock"}, model="mock", prompt_version="p", duration_ms=12)
    assert bare["input_tokens"] is None and bare["est_cost_usd"] is None and bare["latency_ms"] == 12 and bare["cost_basis"] is None


def test_totals_sum_only_what_was_reported():
    assert totals([])["tokens_reported"] is False and totals([])["est_cost_usd"] is None
    steps = [{"kind": "model_call", "duration_ms": 10, "telemetry": {"model_calls": 2, "input_tokens": 10, "output_tokens": 5, "latency_ms": 40,
                                                                      "est_cost_usd": 0.001, "cost_basis": "b", "retries": 1}},
             {"kind": "model_turn", "duration_ms": 5, "telemetry": {"model_calls": 1, "input_tokens": None, "output_tokens": None, "latency_ms": 20}},
             {"kind": "tool_call", "duration_ms": 1}]
    t = totals(steps)
    assert (t["model_calls"], t["model_turns"], t["tool_calls"], t["retries"]) == (3, 1, 1, 1)
    assert (t["input_tokens"], t["output_tokens"], t["model_latency_ms"], t["wall_ms"], t["est_cost_usd"]) == (10, 5, 60, 16, 0.001)


def test_run_trace_exposes_per_step_telemetry_and_totals(tmp_path, providers, image_bytes, area):
    meta, geography = providers
    scripted = Scripted(meta.graph, [searches(), decide()])
    scripted.model = "muse-spark-1.3"
    with TestClient(create_app(tmp_path, scripted, geography)) as client:
        created = create_run(client, image_bytes, area)
        url, headers, run = await_run(client, created)
        body = client.get(url + "/trace", headers=headers).json()
    turns = [s for s in body["steps"] if s["kind"] == "model_turn"]
    assert len(turns) == 2 and all(s["telemetry"]["prompt_version"] == "match-agent-v1" for s in turns)
    assert turns[0]["telemetry"]["input_tokens"] == 10 and turns[0]["telemetry"]["output_tokens"] == 4
    assert turns[0]["telemetry"]["est_cost_usd"] == 0.000026 and turns[0]["telemetry"]["model"] == "muse-spark-1.3"
    reader = body["steps"][0]["telemetry"]
    assert reader["input_tokens"] is None and reader["model_calls"] == 1 and reader["prompt_version"] == "extract-v1"
    totals_ = body["totals"]
    assert totals_ == run["totals"] and totals_["model_calls"] == 3 and totals_["model_turns"] == 2 and totals_["tool_calls"] == 8
    assert (totals_["input_tokens"], totals_["output_tokens"]) == (20, 8) and totals_["est_cost_usd"] == 0.000052
    assert "UNVERIFIED" in totals_["cost_basis"] and body["agent"]["status"] == "completed"
    assert body["prices"]["model"]["muse-spark-1.3"]["verified"] is False
