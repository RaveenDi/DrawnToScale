"""Offline deterministic providers for demos and tests. They test plumbing only: they are
NOT evidence of model quality. MockProvider stands in for muse-spark-1.3; FixtureGeography
serves a published OSM snapshot so no network is needed."""
from difflib import SequenceMatcher
import json
from pathlib import Path

from .contracts import Area, Candidate, Proposals, SketchGraph
from .llm import AgentTurn, ToolCall
from .providers import ATTRIBUTION, LICENSE_URL, ProviderError, normalized_name

FIXTURES = Path(__file__).resolve().parents[3] / "evals" / "fixtures"
MOCK_MODEL = "mock-deterministic (stands in for muse-spark-1.3)"


class MockProvider:
    configured = True
    name = "mock"
    model = MOCK_MODEL
    layout = "demo-sketch"  # fixture name under evals/fixtures; the eval harness switches it per manifest item (mock only)

    def extract(self, image, context):
        layout = json.loads((FIXTURES / f"{self.layout}.json").read_text(encoding="utf-8"))
        graph = SketchGraph(north_direction=layout["north_direction"], landmarks=layout["landmarks"], relations=[],
                            unresolved=[f"MOCK MODE: this is the canned {self.layout} layout, whatever image was uploaded."])
        return graph, {"provider": "mock", "live": False}

    def match(self, graph, shortlist, hint=None):
        """First pass: naive word-overlap pick (plausible but sometimes wrong, like a weak model).
        Re-match (hint given): best string similarity over the allowed, non-rejected candidates."""
        rejected = (hint or {}).get("rejected", {})
        out = []
        for p in graph.landmarks:
            options = [c for c in shortlist.get(p.id, []) if c["id"] not in rejected.get(p.id, [])]
            if hint and p.id not in rejected:  # re-match only touches flagged landmarks
                options = []
            if not options:
                out.append({"sketch_id": p.id, "candidate_id": None, "evidence": "No acceptable candidate; abstaining."})
                continue
            words = set(normalized_name(p.label).split())
            if hint:
                score = lambda c: SequenceMatcher(None, normalized_name(p.label), normalized_name(c["label"])).ratio()
            else:
                score = lambda c: (len(words & set(normalized_name(c["label"]).split())), options.index(c))  # ties go to the later candidate: a deliberately weak first pass
            best = max(options, key=score)
            out.append({"sketch_id": p.id, "candidate_id": best["id"],
                        "evidence": f'Mock proposal: label "{p.label}" resembles "{best["label"]}" ({best["type"]}).'})
        return Proposals(matches=out), {"provider": "mock", "live": False}


    def agent_turn(self, system, transcript, tools):
        """Scripted deterministic policy for the same tool loop the live model drives (plumbing only).
        Turns: search -> get_candidate -> propose+finish; after Verifier feedback: search -> distance_between -> propose+finish.
        The first pass is deliberately naive (word overlap, ties to the later candidate) so the Verifier rejects
        one anchor and the recovery path runs; the recovery pass picks by best string similarity."""
        marks = json.loads(transcript[0]["content"])["sketch"]["landmarks"]
        results = {m["tool_call_id"]: json.loads(m["content"]) for m in transcript if m["role"] == "tool"}
        proposed, segment, rejected, made = {}, [], {}, 0
        for m in transcript[1:]:
            if m["role"] == "assistant":
                for c in m["tool_calls"]:
                    made += 1
                    args, result = json.loads(c.arguments), results.get(c.id) or {}
                    segment.append((c.name, args, result))
                    if c.name == "propose_match" and result.get("ok"):
                        proposed[args["sketch_id"]] = args["candidate_id"]
            elif m["role"] == "user" and m["content"].startswith("{"):
                feedback = json.loads(m["content"]).get("verifier_feedback")
                if feedback:
                    rejected, segment = feedback["rejected_candidate_ids_by_sketch_id"], []
                    for sketch_id in rejected:
                        proposed.pop(sketch_id, None)
        recovery = bool(rejected)
        targets = [p for p in marks if p["id"] in rejected] if recovery else marks

        def call(name, **args):
            nonlocal made
            made += 1
            return ToolCall(f"mock-{made}", name, json.dumps(args))

        def turn(*calls):
            return AgentTurn(None, list(calls), {"provider": "mock", "live": False, "prompt_version": "match-agent-v1"})
        found = {a["query"]: r["results"] for name, a, r in segment if name == "search_candidates" and r.get("ok")}
        missing = [p for p in targets if p["label"] not in found]
        if missing:
            return turn(*[call("search_candidates", query=p["label"]) for p in missing])
        picks = {}
        for p in targets:
            options = [c for c in found[p["label"]] if c["id"] not in rejected.get(p["id"], [])]
            words = set(normalized_name(p["label"]).split())
            if recovery:
                score = lambda c, p=p: SequenceMatcher(None, normalized_name(p["label"]), normalized_name(c["label"])).ratio()
            else:
                score = lambda c, words=words, options=options: (len(words & set(normalized_name(c["label"]).split())), options.index(c))
            picks[p["id"]] = max(options, key=score) if options else None
        chosen = [c for c in picks.values() if c]
        done = [name for name, _, _ in segment]
        if chosen and not recovery and "get_candidate" not in done:
            return turn(*[call("get_candidate", id=c["id"]) for c in chosen])
        anchors = [cid for cid in proposed.values() if cid]
        if recovery and chosen and anchors and "distance_between" not in done:
            return turn(call("distance_between", candidate_a=chosen[0]["id"], candidate_b=anchors[0]))
        calls = []
        for p in targets:
            c = picks[p["id"]]
            evidence = (f'Mock proposal{" after geometry feedback" if recovery else ""}: label "{p["label"]}" resembles "{c["label"]}" ({c["type"]}).'
                        if c else "No acceptable candidate; abstaining.")
            calls.append(call("propose_match", sketch_id=p["id"], candidate_id=c["id"] if c else None, evidence=evidence))
        return turn(*calls, call("finish"))


class FixtureGeography:
    def __init__(self, path=None):
        self.path = Path(path or FIXTURES / "negombo-candidates.json")

    def retrieve(self, area: Area):
        data = json.loads(self.path.read_text(encoding="utf-8"))
        candidates = [Candidate.model_validate(c) for c in data["candidates"] if area.contains(c["longitude"], c["latitude"])]
        if not candidates:
            raise ProviderError("Mock mode only has Negombo data; choose the Negombo area")
        return candidates, {"provider": "OpenStreetMap snapshot (offline fixture)", "attribution": ATTRIBUTION,
                            "license_url": LICENSE_URL, "retrieved_at": data["source"], "cached": True,
                            "candidate_count": len(candidates), "note": data["note"]}
