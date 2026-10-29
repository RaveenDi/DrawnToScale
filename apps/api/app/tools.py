"""Deterministic server-side tools for the matching agent. The model passes IDs and short text only;
every coordinate comes from stored OSM candidates, and every bad call becomes a tool error the model
can read, never an exception."""
import json

from pydantic import Field, ValidationError
from pyproj import Geod

from .contracts import Contract, Identifier
from .providers import rank_candidates


class SearchArgs(Contract):
    query: str = Field(min_length=1, max_length=120)
    kind: str | None = Field(default=None, max_length=40)


class GetArgs(Contract):
    id: Identifier


class DistanceArgs(Contract):
    candidate_a: Identifier
    candidate_b: Identifier


class ProposeArgs(Contract):
    sketch_id: Identifier
    candidate_id: Identifier | None
    evidence: str = Field(min_length=1, max_length=2000)


class FinishArgs(Contract):
    pass


# Provider-neutral tool specs (JSON Schema). The provider turns these into its wire format.
TOOL_SPECS = [
    {"name": "search_candidates",
     "description": "Fuzzy-search the stored map candidates in the selected area by name (labels and aliases). Returns up to 6 {id,label,type,aliases}. Use a landmark label or part of it; optionally filter by feature type such as 'school'.",
     "parameters": SearchArgs.model_json_schema()},
    {"name": "get_candidate",
     "description": "Full stored record (label, aliases, type, longitude, latitude, source_url, position_kind) for one candidate id returned by search_candidates.",
     "parameters": GetArgs.model_json_schema()},
    {"name": "distance_between",
     "description": "Geodesic distance in metres between two stored candidates. Use it to sanity-check that proposed matches are plausible relative to each other.",
     "parameters": DistanceArgs.model_json_schema()},
    {"name": "propose_match",
     "description": "Record your decision for ONE sketch landmark: a candidate id returned by search_candidates, or null to abstain (unmapped, ambiguous or unknown). Evidence: short reason (max 250 chars). Calling it again for the same sketch_id replaces the earlier decision.",
     "parameters": ProposeArgs.model_json_schema()},
    {"name": "finish",
     "description": "End the task once every sketch landmark has a decision (a candidate id or null). A deterministic geometry check then runs and may send feedback.",
     "parameters": FinishArgs.model_json_schema()},
]
TOOL_NAMES = [t["name"] for t in TOOL_SPECS]
_ARGS = {"search_candidates": SearchArgs, "get_candidate": GetArgs, "distance_between": DistanceArgs,
         "propose_match": ProposeArgs, "finish": FinishArgs}


def fail(message):
    return {"ok": False, "error": message}


def error_summary(exc):
    return "; ".join(f'{".".join(str(p) for p in e["loc"]) or "arguments"}: {e["msg"]}' for e in exc.errors()[:4])


class MatchTools:
    def __init__(self, graph, candidates):
        self.sketch_ids = [p.id for p in graph.landmarks]
        self.pool = {c.id: c for c in candidates}
        self.seen = set()          # candidate IDs the model has actually been shown
        self.proposals = {}        # sketch_id -> {"candidate_id", "evidence"}
        self.rejected = {}         # sketch_id -> candidate IDs the Verifier rejected
        self.finished = False
        self.geod = Geod(ellps="WGS84")

    @property
    def undecided(self):
        return [s for s in self.sketch_ids if s not in self.proposals]

    def rows(self):
        return [{"sketch_id": s, **self.proposals[s]} for s in self.sketch_ids if s in self.proposals]

    def reject(self, sketch_ids):
        """Verifier feedback: drop these picks and remember the IDs so they cannot be proposed again."""
        for s in sketch_ids:
            old = self.proposals.pop(s, None)
            if old and old["candidate_id"]:
                self.rejected.setdefault(s, []).append(old["candidate_id"])
        self.finished = False

    def call(self, name, raw):
        """Run one tool call. `raw` is the model's JSON argument text. Always returns a dict with `ok`."""
        if name not in _ARGS:
            return fail(f"Unknown tool {name!r}. Available tools: {', '.join(TOOL_NAMES)}.")
        try:
            data = json.loads(raw) if isinstance(raw, str) and raw.strip() else {}
        except ValueError:
            return fail("Arguments are not valid JSON. Send one JSON object matching the tool's parameters.")
        if not isinstance(data, dict):
            return fail("Arguments must be a JSON object.")
        try:
            args = _ARGS[name].model_validate(data)
        except ValidationError as exc:
            return fail("Invalid arguments: " + error_summary(exc))
        return getattr(self, "_" + name)(args)

    def _search_candidates(self, a):
        found = rank_candidates(a.query, self.pool.values(), a.kind)
        self.seen.update(c.id for c in found)
        results = [{"id": c.id, "label": c.label, "type": c.type, "aliases": c.aliases[:3]} for c in found]
        out = {"ok": True, "count": len(results), "results": results}
        if not results:
            out["note"] = "Nothing in the stored candidates resembles this query. If no other query helps, abstain with candidate_id null."
        return out

    def _get_candidate(self, a):
        c = self.pool.get(a.id)
        if not c:
            return fail("Unknown candidate id. Only ids returned by search_candidates exist.")
        self.seen.add(c.id)
        return {"ok": True, "candidate": {**c.model_dump(exclude={"longitude", "latitude"}),
                                          "longitude": round(c.longitude, 6), "latitude": round(c.latitude, 6)}}

    def _distance_between(self, a):
        first, second = self.pool.get(a.candidate_a), self.pool.get(a.candidate_b)
        if not first or not second:
            return fail("Both ids must be stored candidates returned by search_candidates.")
        return {"ok": True, "metres": round(self.geod.inv(first.longitude, first.latitude, second.longitude, second.latitude)[2], 1)}

    def _propose_match(self, a):
        if a.sketch_id not in self.sketch_ids:
            return fail(f"Unknown sketch_id. Valid ids: {', '.join(self.sketch_ids)}.")
        if a.candidate_id is not None:
            if a.candidate_id not in self.pool:
                return fail("candidate_id is not a stored candidate. Use only ids returned by search_candidates, or null.")
            if a.candidate_id not in self.seen:
                return fail("candidate_id was not returned by a search_candidates/get_candidate call in this run. Search first.")
            if a.candidate_id in self.rejected.get(a.sketch_id, []):
                return fail("The geometry check rejected this candidate for this landmark. Choose another or null.")
            clash = next((s for s, p in self.proposals.items() if s != a.sketch_id and p["candidate_id"] == a.candidate_id), None)
            if clash:
                return fail(f"{a.candidate_id} is already proposed for {clash}. Two landmarks cannot share one place; pick another or null.")
        note = "evidence shortened to 250 characters" if len(a.evidence) > 250 else None
        self.proposals[a.sketch_id] = {"candidate_id": a.candidate_id, "evidence": a.evidence[:250]}
        out = {"ok": True, "recorded": {"sketch_id": a.sketch_id, "candidate_id": a.candidate_id}, "undecided": self.undecided}
        return {**out, "note": note} if note else out

    def _finish(self, a):
        if self.undecided:
            return fail(f"Cannot finish: no decision yet for {', '.join(self.undecided)}. Call propose_match for each (candidate_id null to abstain).")
        self.finished = True
        return {"ok": True, "status": "finished; geometry check follows"}
