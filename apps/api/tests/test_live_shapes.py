"""Replay of SANITIZED recordings of the first real Meta Model API calls (2026-10-30, synthetic sketch only).

The fixtures in fixtures/live/ are {request, response} pairs captured from POST /v1/chat/completions: images are
replaced by their sha256, no auth header or account id was ever stored. They pin the *verified* wire shapes:
structured output (strict json_schema), tool calls (arguments is a JSON string, tool_choice "auto") and how tool
results go back. Responses are served by a mock transport; nothing here touches the network."""
import json
from pathlib import Path

import httpx

from app.agent import MatchAgent
from app.config import Settings, PRICES_USD_PER_MTOK
from app.contracts import Candidate
from app.llm import Transport
from app.providers import MetaProvider
from app.telemetry import estimate_cost
from app.tools import TOOL_SPECS, MatchTools
from app.verifier import verify

LIVE = Path(__file__).parent / "fixtures" / "live"
EVALS = Path(__file__).parents[3] / "evals" / "fixtures"


def fixture(name):
    return json.loads((LIVE / f"{name}.json").read_text(encoding="utf-8"))


def provider_serving(names, seen):
    queue = [fixture(n) for n in names]

    def respond(request):
        seen.append(json.loads(request.content))
        record = queue.pop(0)
        return httpx.Response(200, json=record["response"])
    transport = Transport("test-only-placeholder", client=httpx.Client(transport=httpx.MockTransport(respond)), sleep=lambda s: None)
    return MetaProvider("test-only-placeholder", transport=transport, max_completion_tokens=1024, reasoning_effort="minimal")


def test_recorded_structured_extraction_parses_and_request_shape_matches_the_live_one():
    recorded, seen = fixture("extract"), []
    graph, telemetry = provider_serving(["extract"], seen).extract(b"\x89PNG-not-decoded", "")
    assert [p.label for p in graph.landmarks] == ["St Sebastian Church", "Maris Stella", "Dutch Fort", "Arpico Super Market", "Bus Terminal"]
    assert graph.north_direction == "up" and len(graph.relations) == 4
    assert telemetry["returned_model"] == "muse-spark-1.3" and telemetry["response_id"].startswith("chatcmpl-")
    assert telemetry["usage"]["prompt_tokens"] == 1950 and telemetry["usage"]["completion_tokens"] == 625
    assert estimate_cost("muse-spark-1.3", 1950, 625) == round((1950 * 1.25 + 625 * 4.25) / 1e6, 6)
    sent = seen[0]
    assert sorted(sent) == sorted(recorded["request"]) == ["max_completion_tokens", "messages", "model", "reasoning_effort", "response_format"]
    assert [m["role"] for m in sent["messages"]] == [m["role"] for m in recorded["request"]["messages"]] == ["system", "user"]
    assert sent["messages"][1]["content"][1]["image_url"]["url"].startswith("data:image/png;base64,")
    rf = sent["response_format"]
    assert rf["type"] == "json_schema" and rf["json_schema"]["strict"] is True
    assert rf["json_schema"]["schema"]["additionalProperties"] is False  # strict mode requires it on every object


def test_recorded_tool_calling_loop_replays_and_tool_results_use_the_verified_wire_shape():
    candidates = [Candidate(**c) for c in json.loads((EVALS / "negombo-candidates.json").read_text(encoding="utf-8"))["candidates"]]
    area = json.loads((EVALS / "negombo-candidates.json").read_text(encoding="utf-8"))["area"]
    seen = []
    provider = provider_serving(["extract", "agent-turn-1", "agent-turn-2", "agent-turn-3", "agent-turn-4"], seen)
    graph, _ = provider.extract(b"\x89PNG-not-decoded", "")
    tools, steps = MatchTools(graph, candidates), []
    image = {"width": 1200, "height": 900}
    agent = MatchAgent(provider, tools, settings=Settings(max_steps=4),
                       verify=lambda rows: verify(graph.model_dump(), rows, [c.model_dump() for c in candidates], area, image),
                       charge=lambda: None, calls_left=lambda: 10, record=steps.append, model="muse-spark-1.3")
    outcome = agent.run(graph)
    assert outcome.status == "partial_step_cap" and outcome.steps == 4 and outcome.tool_calls == 17
    picked = {sid: row["candidate_id"] for sid, row in tools.proposals.items()}  # what the model proposed
    assert picked == {"st_sebastian_church": "osm:way/1472244684", "maris_stella": "osm:node/1268340441", "dutch_fort": "osm:way/196057979",
                       "arpico_super_market": "osm:way/581075989", "bus_terminal": "osm:way/97913613"}
    # the deterministic geometry check then judged the picks against the model's own (imprecise) sketch coordinates
    assert sorted(outcome.summary()["abstained"]) == ["arpico_super_market", "dutch_fort"]
    turns = seen[1:]
    assert len(turns) == 4
    for body in turns:
        assert body["tool_choice"] == "auto" and body["reasoning_effort"] == "minimal"
        assert [t["function"]["name"] for t in body["tools"]] == [t["name"] for t in TOOL_SPECS]
        assert all(t["type"] == "function" and set(t["function"]) == {"name", "description", "parameters"} for t in body["tools"])
    # turn 2 must carry the whole assistant message (with tool_calls) and one tool message per call, ids matching
    messages = turns[1]["messages"]
    assistant = next(m for m in messages if m["role"] == "assistant")
    wanted = [c["id"] for c in assistant["tool_calls"]]
    answered = [m["tool_call_id"] for m in messages if m["role"] == "tool"]
    assert wanted == answered and len(wanted) == 5
    assert all(c["type"] == "function" and isinstance(c["function"]["arguments"], str) for c in assistant["tool_calls"])
    assert all(isinstance(m["content"], str) for m in messages if m["role"] == "tool")
    # the live replies: arguments are JSON strings, finish_reason is tool_calls, content may be text
    for name in ("agent-turn-1", "agent-turn-2", "agent-turn-3", "agent-turn-4"):
        choice = fixture(name)["response"]["choices"][0]
        assert choice["finish_reason"] == "tool_calls" and all(isinstance(c["function"]["arguments"], str) for c in choice["message"]["tool_calls"])


def test_price_table_is_the_published_standard_rate():
    price = PRICES_USD_PER_MTOK["muse-spark-1.3"]
    assert (price["input"], price["output"], price["verified"]) == (1.25, 4.25, True)
