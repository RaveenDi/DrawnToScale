import json
from itertools import count

from fastapi.testclient import TestClient
import httpx
import pytest

from app.agent import MatchAgent
from app.config import Settings
from app.llm import AgentTurn, ProviderError, ToolCall, Transport
from app.main import create_app
from app.providers import MetaProvider
from app.tools import TOOL_SPECS, MatchTools
from conftest import FakeMeta
from test_workflow import await_run, create_run

ids = count(1)


def turn(*calls, content=None):
    made = [ToolCall(f"c{next(ids)}", name, args if isinstance(args, str) else json.dumps(args)) for name, args in calls]
    return AgentTurn(content, made, {"provider": "scripted", "usage": {"prompt_tokens": 10, "completion_tokens": 4}})


def searches():
    return turn(*[("search_candidates", {"query": q}) for q in ("Sample Library", "Sample School", "Sample Park")])


def decide():
    return turn(("propose_match", {"sketch_id": "p1", "candidate_id": "synthetic:0", "evidence": "Exact label match"}),
                ("propose_match", {"sketch_id": "p2", "candidate_id": "synthetic:1", "evidence": "Exact label match"}),
                ("propose_match", {"sketch_id": "p3", "candidate_id": "synthetic:2", "evidence": "Exact label match"}),
                ("propose_match", {"sketch_id": "p4", "candidate_id": None, "evidence": "Unmapped stall"}), ("finish", {}))


class Scripted(FakeMeta):
    """Test double with a scripted tool-calling policy; entries are AgentTurns or exceptions to raise."""
    def __init__(self, graph, script):
        super().__init__(graph)
        self.script, self.transcripts = list(script), []

    def agent_turn(self, system, transcript, tools):
        self.transcripts.append(list(transcript))
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def run_with(tmp_path, graph, geography, script, image_bytes, area):
    meta = Scripted(graph, script)
    with TestClient(create_app(tmp_path, meta, geography)) as client:
        created = create_run(client, image_bytes, area)
        url, headers, run = await_run(client, created)
        return meta, run, client.get(url + "/trace", headers=headers).json()


def tool_steps(trace):
    return [s for s in trace["steps"] if s["kind"] == "tool_call"]


def test_loop_happy_path_records_every_turn_and_tool(tmp_path, providers, image_bytes, area):
    meta, geography = providers
    meta, run, trace = run_with(tmp_path, meta.graph, geography, [searches(), decide()], image_bytes, area)
    assert run["status"] == "needs_confirmation" and run["agent"]["status"] == "completed"
    assert [p["candidate_id"] for p in run["proposals"]] == ["synthetic:0", "synthetic:1", "synthetic:2", None]
    assert run["model_calls"] == 3 and run["agent"]["steps"] == 2 and run["agent"]["tool_calls"] == 8
    assert [s["tool"] for s in tool_steps(trace)] == ["search_candidates"] * 3 + ["propose_match"] * 4 + ["finish"]
    kinds = [s["kind"] for s in trace["steps"]]
    assert kinds.count("model_turn") == 2 and kinds.count("tool_call") == 8
    assert all(s["status"] == "ok" for s in tool_steps(trace))
    assert trace["steps"][-1]["agent"] == "HumanReview"


def test_bad_tool_calls_become_tool_errors_not_crashes(tmp_path, providers, image_bytes, area):
    meta, geography = providers
    bad = turn(("delete_everything", {}), ("search_candidates", "{not json"), ("search_candidates", {"query": ""}),
               ("propose_match", {"sketch_id": "p1", "candidate_id": "synthetic:0", "evidence": "never searched"}),
               ("propose_match", {"sketch_id": "p1", "candidate_id": "invented:1", "evidence": "x"}),
               ("propose_match", {"sketch_id": "p1", "candidate_id": None, "evidence": "x", "latitude": 7.1}),
               ("propose_match", {"sketch_id": "nope", "candidate_id": None, "evidence": "x"}),
               ("finish", {}))
    script = [bad, searches(), decide()]
    meta, run, trace = run_with(tmp_path, meta.graph, geography, script, image_bytes, area)
    assert run["status"] == "needs_confirmation" and run["agent"]["status"] == "completed"
    errors = [s for s in tool_steps(trace) if s["status"] == "tool_error"]
    assert len(errors) == 8
    results = {m["tool_call_id"]: json.loads(m["content"]) for m in meta.transcripts[1] if m["role"] == "tool"}
    messages = " | ".join(r["error"] for r in results.values())
    for fragment in ("Unknown tool", "not valid JSON", "Invalid arguments", "not returned by a search", "not a stored candidate", "Valid ids", "Cannot finish"):
        assert fragment in messages
    assert "latitude" in messages  # extra fields (a model-supplied coordinate) are rejected by the strict contract


def test_tools_enforce_uniqueness_rejection_and_stored_coordinates(graph, candidates):
    tools = MatchTools(graph, candidates)
    tools.call("search_candidates", json.dumps({"query": "Sample"}))
    ok = lambda sid, cid: tools.call("propose_match", json.dumps({"sketch_id": sid, "candidate_id": cid, "evidence": "e"}))["ok"]
    assert ok("p1", "synthetic:0") and not ok("p2", "synthetic:0")  # one place cannot serve two landmarks
    tools.reject(["p1"])
    assert "p1" in tools.undecided and not ok("p1", "synthetic:0")  # rejected ids are gone for that landmark
    detail = tools.call("get_candidate", json.dumps({"id": "synthetic:1"}))["candidate"]
    assert (detail["longitude"], detail["latitude"]) == (round(candidates[1].longitude, 6), round(candidates[1].latitude, 6))
    metres = tools.call("distance_between", json.dumps({"candidate_a": "synthetic:0", "candidate_b": "synthetic:1"}))["metres"]
    assert metres > 100
    assert not tools.call("distance_between", json.dumps({"candidate_a": "synthetic:0", "candidate_b": "made-up"}))["ok"]


def test_step_cap_returns_partial_result_with_honest_status(tmp_path, monkeypatch, providers, image_bytes, area):
    monkeypatch.setenv("AGENT_MAX_STEPS", "3")
    meta, geography = providers
    script = [turn(("propose_match", {"sketch_id": "p4", "candidate_id": None, "evidence": "Unmapped stall"}))] + [searches() for _ in range(9)]
    meta, run, trace = run_with(tmp_path, meta.graph, geography, script, image_bytes, area)
    assert run["status"] == "needs_confirmation"
    assert run["agent"]["status"] == "partial_step_cap" and run["agent"]["steps"] == 3 and len(meta.script) == 7
    assert run["model_calls"] == 4  # extraction + 3 turns, no more
    assert [p["candidate_id"] for p in run["proposals"]] == [None] * 4
    assert "Step cap" in run["proposals"][0]["evidence"] and run["proposals"][3]["evidence"] == "Unmapped stall"
    stop = [s for s in trace["steps"] if s["kind"] == "stop"]
    assert stop and stop[0]["status"] == "partial" and "Step cap" in stop[0]["output_summary"]


def test_model_call_cap_returns_partial_result(tmp_path, monkeypatch, providers, image_bytes, area):
    monkeypatch.setenv("AGENT_MAX_MODEL_CALLS", "3")
    meta, geography = providers
    meta, run, _ = run_with(tmp_path, meta.graph, geography, [searches() for _ in range(9)], image_bytes, area)
    assert run["agent"]["status"] == "partial_call_cap" and run["model_calls"] == 3 and run["agent"]["steps"] == 2


def test_text_only_or_unusable_turns_abstain_with_clear_status(tmp_path, providers, image_bytes, area):
    meta, geography = providers
    meta, run, _ = run_with(tmp_path, meta.graph, geography,
                            [turn(content="I think the library is the first one."), turn(content="Done!"), searches()], image_bytes, area)
    assert run["agent"]["status"] == "abstained_invalid_output" and len(meta.script) == 1
    assert all(p["candidate_id"] is None for p in run["proposals"])
    assert meta.transcripts[1][-1]["role"] == "user" and "tool calls only" in meta.transcripts[1][-1]["content"]  # one nudge
    meta, run, _ = run_with(tmp_path / "b", meta.graph, geography,
                            [turn(("finish", "{broken")), turn(("finish", "{broken")), searches()], image_bytes, area)
    assert run["agent"]["status"] == "abstained_invalid_output"


def test_provider_failure_first_turn_fails_run_but_later_turn_keeps_partial(tmp_path, providers, image_bytes, area):
    meta, geography = providers
    _, run, trace = run_with(tmp_path, meta.graph, geography, [ProviderError("Provider returned HTTP 503 after 2 retries.")], image_bytes, area)
    assert run["status"] == "failed" and "503" in run["error"] and run["proposals"] == []
    assert trace["steps"][-1]["status"] == "failed"
    _, run, _ = run_with(tmp_path / "b", meta.graph, geography,
                         [turn(("search_candidates", {"query": "Sample Library"})), ProviderError("Provider network/timeout failure.")], image_bytes, area)
    assert run["status"] == "needs_confirmation" and run["agent"]["status"] == "stopped_provider_error"
    assert "timeout" in run["agent"]["detail"] and all(p["candidate_id"] is None for p in run["proposals"])


def test_recovery_happens_once_and_geometry_failure_abstains(graph, candidates):
    verdicts, script = [], [searches(), decide(),
                            turn(("propose_match", {"sketch_id": "p1", "candidate_id": "synthetic:0", "evidence": "again"}),
                                 ("propose_match", {"sketch_id": "p1", "candidate_id": None, "evidence": "give up"}), ("finish", {})),
                            searches()]
    class Provider:
        def agent_turn(self, system, transcript, tools):
            self.last = transcript
            return script.pop(0)
    def verify(rows):
        verdicts.append(rows)
        return {"status": "rejected", "outliers": ["p1"], "fit_residuals_m": {"p1": 400.0}}
    provider, steps = Provider(), []
    agent = MatchAgent(provider, MatchTools(graph, candidates), settings=Settings(), verify=verify, charge=lambda: None,
                       calls_left=lambda: 99, record=steps.append, model="m")
    outcome = agent.run(graph)
    assert outcome.status == "completed" and outcome.recovered and len(verdicts) == 2 and len(script) == 1  # no third round
    feedback = next(m for m in provider.last if m["role"] == "user" and "verifier_feedback" in m["content"])
    assert json.loads(feedback["content"])["verifier_feedback"]["rejected_candidate_ids_by_sketch_id"] == {"p1": ["synthetic:0"]}
    assert outcome.proposals[0]["candidate_id"] is None and "failed twice" in outcome.proposals[0]["evidence"]
    assert [s["status"] for s in steps if s["kind"] == "tool_call" and s["tool"] == "propose_match"].count("tool_error") == 1


def test_meta_provider_wire_shape_and_telemetry(tmp_path, graph):
    seen = []
    def respond(request):
        seen.append(json.loads(request.content))
        calls = [{"id": "call_1", "type": "function", "function": {"name": "search_candidates", "arguments": json.dumps({"query": "x"})}},
                 {"id": "call_2", "type": "function", "function": {"name": "finish", "arguments": {}}}]
        return httpx.Response(200, json={"model": "test-double", "id": "t1", "usage": {"prompt_tokens": 120, "completion_tokens": 30},
                                         "choices": [{"finish_reason": "tool_calls", "message": {"content": None, "tool_calls": calls}}]})
    transport = Transport("sk-test", client=httpx.Client(transport=httpx.MockTransport(respond)), recordings=tmp_path)
    provider = MetaProvider("sk-test", transport=transport)
    previous = turn(("search_candidates", {"query": "x"}))
    transcript = [{"role": "user", "content": "{}"}, {"role": "assistant", "content": None, "tool_calls": previous.tool_calls},
                  {"role": "tool", "tool_call_id": previous.tool_calls[0].id, "name": "search_candidates", "content": '{"ok": true}'}]
    result = provider.agent_turn("SYSTEM", transcript, TOOL_SPECS)
    body = seen[0]
    assert body["tool_choice"] == "auto" and [t["function"]["name"] for t in body["tools"]] == [t["name"] for t in TOOL_SPECS]
    assert all(t["type"] == "function" and t["function"]["parameters"]["type"] == "object" for t in body["tools"])
    roles = [m["role"] for m in body["messages"]]
    assert roles == ["system", "user", "assistant", "tool"]
    assert body["messages"][2]["tool_calls"][0]["function"]["arguments"] == previous.tool_calls[0].arguments
    assert body["messages"][3]["tool_call_id"] == previous.tool_calls[0].id
    assert [c.name for c in result.tool_calls] == ["search_candidates", "finish"] and result.tool_calls[1].arguments == "{}"
    assert result.telemetry["usage"] == {"prompt_tokens": 120, "completion_tokens": 30}
    replay = MetaProvider("", transport=Transport(mode="replay", recordings=tmp_path))
    assert [c.name for c in replay.agent_turn("SYSTEM", transcript, TOOL_SPECS).tool_calls] == ["search_candidates", "finish"]
    assert len(seen) == 1
