"""Offline deterministic providers for demos and tests. They test plumbing only: they are
NOT evidence of model quality. MockProvider stands in for muse-spark-1.3; FixtureGeography
serves a published OSM snapshot so no network is needed."""
from difflib import SequenceMatcher
import json
from pathlib import Path

from .contracts import Area, Candidate, Proposals, SketchGraph
from .providers import ATTRIBUTION, LICENSE_URL, ProviderError, normalized_name

FIXTURES = Path(__file__).resolve().parents[3] / "evals" / "fixtures"
MOCK_MODEL = "mock-deterministic (stands in for muse-spark-1.3)"


class MockProvider:
    configured = True
    name = "mock"
    model = MOCK_MODEL

    def extract(self, image, context):
        layout = json.loads((FIXTURES / "demo-sketch.json").read_text(encoding="utf-8"))
        graph = SketchGraph(north_direction=layout["north_direction"], landmarks=layout["landmarks"], relations=[],
                            unresolved=["MOCK MODE: this is the canned demo-sketch layout, whatever image was uploaded."])
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
