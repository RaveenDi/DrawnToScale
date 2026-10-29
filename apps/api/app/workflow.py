from concurrent.futures import ThreadPoolExecutor
import threading
import time

from .contracts import Area, SketchGraph
from .geometry import align
from .providers import ProviderError, shortlist_candidates, validate_proposals
from .storage import EDITABLE, Conflict
from .verifier import verify


class Workflow:
    def __init__(self, store, meta, geography):
        self.store, self.meta, self.geography = store, meta, geography
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="sketch-worker")
        self.slot = threading.BoundedSemaphore(1)

    def reserve(self):
        if not self.slot.acquire(blocking=False):
            raise Conflict("One sketch is already processing. Wait for it to finish.")

    def submit_reserved(self, run_id):
        try:
            self.executor.submit(self._execute, run_id)
        except Exception:
            self.slot.release()
            raise

    def close(self):
        self.executor.shutdown(wait=True)

    def stage(self, run_id, stage, **changes):
        return self.store.update(run_id, lambda r: r.update(status=stage, **changes), event={"kind": stage})

    def count_call(self, run_id):
        def increment(run):
            if run["model_calls"] >= 4:
                raise ProviderError("Four model attempts already used for this run; create a new run after review")
            run["model_calls"] += 1
        self.store.update(run_id, increment, event={"kind": "model_call_started"})

    def _execute(self, run_id):
        try:
            self.process(run_id)
        except ProviderError as exc:
            self.stage(run_id, "failed", error=str(exc))
        except Exception:
            # Do not expose provider payloads, request headers or secret-bearing exception strings.
            self.stage(run_id, "failed", error="Processing failed unexpectedly. Saved stages are retained for explicit retry.")
        finally:
            self.slot.release()

    def _add_step(self, run_id, step):
        self.store.update(run_id, lambda r: r.setdefault("trace", []).append(step))

    def _step(self, run_id, agent, role, model, input_summary, fn):
        """Run fn() -> (value, output_summary, status); record the agent step either way."""
        started = time.monotonic()
        base = {"agent": agent, "role": role, "model": model, "input_summary": input_summary}
        try:
            value, output, status = fn()
        except Exception as exc:
            msg = str(exc) if isinstance(exc, ProviderError) else "Unexpected error"
            self._add_step(run_id, {**base, "output_summary": msg, "duration_ms": round((time.monotonic() - started) * 1000),
                                    "status": "failed"})
            raise
        self._add_step(run_id, {**base, "output_summary": output, "duration_ms": round((time.monotonic() - started) * 1000),
                                "status": status})
        return value

    def process(self, run_id):
        run = self.store.get(run_id)
        if run["attempts"] >= 3:
            raise ProviderError("Three processing attempts used; review this run before starting another")
        model = getattr(self.meta, "model", "test-double")
        run = self.stage(run_id, "extracting", attempts=run["attempts"] + 1, error=None)
        if run["graph"] is None:
            def read():
                self.count_call(run_id)
                graph, telemetry = self.meta.extract(self.store.image_path(run_id).read_bytes(), run["context"])
                return (graph, telemetry), f"{len(graph.landmarks)} landmarks, north={graph.north_direction}", "ok"
            graph, telemetry = self._step(run_id, "SketchReader", "Read the sketch image into a structured landmark graph", model,
                                          f'image {run["image"]["width"]}x{run["image"]["height"]}, schema SketchGraph', read)
            run = self.stage(run_id, "retrieving", graph=graph.model_dump(), original_graph=graph.model_dump(),
                             telemetry=[*run["telemetry"], {"stage": "extraction", **telemetry}])
        else:
            graph = SketchGraph.model_validate(run["graph"])
            self._add_step(run_id, {"agent": "SketchReader", "role": "Read the sketch image", "model": model,
                                    "input_summary": "saved result", "output_summary": f"{len(graph.landmarks)} landmarks reused",
                                    "duration_ms": 0, "status": "reused"})
            run = self.stage(run_id, "retrieving")
        if run["candidate_manifest"] is None:
            def retrieve():
                candidates, manifest = self.geography.retrieve(Area.model_validate(run["area"]))
                area = Area.model_validate(run["area"])
                if len({c.id for c in candidates}) != len(candidates) or any(not area.contains(c.longitude, c.latitude) for c in candidates):
                    raise ProviderError("Candidate data has duplicate IDs or coordinates outside the selected area")
                shortlist = shortlist_candidates(graph, candidates)
                n = sum(1 for v in shortlist.values() if v)
                return (candidates, manifest, shortlist), f"{len(candidates)} OSM candidates, {n}/{len(shortlist)} landmarks shortlisted", "ok"
            candidates, manifest, shortlist = self._step(run_id, "GeoRetriever", "Fetch OSM candidates and shortlist per landmark",
                                                         "none (deterministic tool)", "area bounding box", retrieve)
            run = self.stage(run_id, "matching", candidates=[c.model_dump() for c in candidates],
                             candidate_manifest=manifest, shortlist=shortlist)
        else:
            run = self.stage(run_id, "matching")
        history = run["telemetry"]
        if graph.landmarks and any(run["shortlist"].values()):
            def match():
                self.count_call(run_id)
                proposals, telemetry = self.meta.match(graph, run["shortlist"])
                validate_proposals(graph, proposals, run["shortlist"])
                n = sum(1 for p in proposals.matches if p.candidate_id)
                return (proposals, telemetry), f"{n} proposed, {len(proposals.matches) - n} abstained", "ok"
            proposals, telemetry = self._step(run_id, "Matcher", "Pick one allowed candidate ID per landmark, or abstain", model,
                                              "sketch graph + allowed candidate IDs only", match)
            matches = [p.model_dump() for p in proposals.matches]
            history = [*history, {"stage": "matching", **telemetry}]
            matches, history = self._verify_loop(run_id, run, graph, matches, history, model)
        else:
            matches = [{"sketch_id": p.id, "candidate_id": None, "evidence": "No candidate found in this area; review coverage or correct the label."} for p in graph.landmarks]
        self._add_step(run_id, {"agent": "HumanReview", "role": "Author confirms or rejects every proposal", "model": "human",
                                "input_summary": f"{len(matches)} proposals", "output_summary": "awaiting your confirmations",
                                "duration_ms": 0, "status": "awaiting_human"})
        self.stage(run_id, "needs_confirmation", proposals=matches, telemetry=history,
                   alignment={"status": "insufficient_anchors", "required": 3, "accepted": 0, "independent_error_m": None})

    def _verify_loop(self, run_id, run, graph, matches, history, model):
        """Verifier may reject outliers; at most ONE bounded re-match (one extra model call)."""
        def check(label):
            def go():
                result = verify(graph.model_dump(), matches, run["candidates"], run["area"], run["image"])
                held = result.get("held_out_error_m")
                out = f'{result["status"]}; outliers={result["outliers"]}' + (f'; held-out median {held["median"]} m' if held else "")
                return result, out, {"pass": "ok", "rejected": "rejected"}.get(result["status"], "skipped")
            n = sum(1 for m in matches if m["candidate_id"])
            return self._step(run_id, "Verifier", "Deterministic geometry check (similarity fit, outliers, held-out error)",
                              "none (deterministic geometry)", f"{label}: {n} proposed anchors", go)
        result = check("first pass")
        if result["status"] != "rejected" or not result["outliers"]:
            return matches, history
        rejected = {s: [m["candidate_id"] for m in matches if m["sketch_id"] == s] for s in result["outliers"]}

        def rematch():
            self.count_call(run_id)
            new, telemetry = self.meta.match(graph, run["shortlist"], hint={"rejected": rejected})
            validate_proposals(graph, new, run["shortlist"])
            return ({p.sketch_id: p for p in new.matches}, telemetry), f"re-evaluated {sorted(rejected)} only", "ok"
        picked, telemetry = self._step(run_id, "Matcher", "Bounded re-match of Verifier-rejected outliers (max 1)", model,
                                       f"outliers {sorted(rejected)}, rejected IDs excluded", rematch)
        history = [*history, {"stage": "rematch", **telemetry}]
        for m in matches:
            if m["sketch_id"] in rejected:
                n = picked[m["sketch_id"]]
                if n.candidate_id is None or n.candidate_id in rejected[m["sketch_id"]]:
                    m.update(candidate_id=None, evidence="Verifier rejected the earlier pick; abstaining.")
                else:
                    m.update(candidate_id=n.candidate_id, evidence="Re-matched after geometry check. " + n.evidence)
        final = check("after re-match")
        for m in matches:  # still inconsistent: abstain rather than propose a geometrically rejected anchor
            if m["sketch_id"] in final["outliers"]:
                m.update(candidate_id=None, evidence="Geometry check failed twice; left for your decision.")
        return matches, history


def apply_confirmations(store, run_id, request):
    def change(run):
        if run["status"] not in EDITABLE:
            raise Conflict("Wait until extraction and matching have finished")
        sketch_ids = {p["id"] for p in run["graph"]["landmarks"]}
        candidate_ids = {c["id"] for c in run["candidates"]}
        decisions = request.decisions
        if len({d.sketch_id for d in decisions}) != len(decisions):
            raise ValueError("Each sketch landmark may be edited only once per request")
        updated = dict(run["confirmations"])
        for d in decisions:
            if d.sketch_id not in sketch_ids or (d.candidate_id is not None and d.candidate_id not in candidate_ids):
                raise ValueError("Choose an existing sketch ID and candidate ID, or null for unmatched")
            updated[d.sketch_id] = {**d.model_dump(), "actor": "user"}
        ids = [d["candidate_id"] for d in updated.values() if d["candidate_id"] is not None]
        if len(ids) != len(set(ids)):
            raise ValueError("Two landmarks cannot confirm the same geographic candidate; correct the sketch first")
        run["confirmations"] = updated
        run["alignment"] = align(run)
        run["status"] = "ready" if set(updated) == sketch_ids else "needs_confirmation"
        for step in run.get("trace", []):
            if step["agent"] == "HumanReview":
                step.update(status="done" if run["status"] == "ready" else "awaiting_human",
                            output_summary=f"{sum(1 for d in updated.values() if d['candidate_id'])} confirmed, "
                                           f"{sum(1 for d in updated.values() if not d['candidate_id'])} left unmatched by you")
    return store.update(run_id, change, revision=request.revision,
                        event={"kind": "user_confirmation", "decisions": [d.model_dump() for d in request.decisions]})


def apply_graph_edit(store, run_id, request):
    def change(run):
        if run["status"] not in EDITABLE:
            raise Conflict("Wait until extraction and matching have finished")
        from .contracts import Candidate
        run["graph"] = request.graph.model_dump()
        run["shortlist"] = shortlist_candidates(request.graph, [Candidate.model_validate(c) for c in run["candidates"]])
        run["proposals"] = [{"sketch_id": p.id, "candidate_id": None,
                              "evidence": "Sketch edited. Review candidate choices again; no extra model call made."}
                             for p in request.graph.landmarks]
        run["confirmations"] = {}
        run["alignment"] = None
        run["status"] = "needs_confirmation"
    return store.update(run_id, change, revision=request.revision,
                        event={"kind": "user_graph_edit", "graph": request.graph.model_dump(), "note": request.note})
