from concurrent.futures import ThreadPoolExecutor
import threading

from .contracts import Area, SketchGraph
from .geometry import align
from .providers import ProviderError, shortlist_candidates, validate_proposals
from .storage import EDITABLE, Conflict


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

    def process(self, run_id):
        run = self.store.get(run_id)
        if run["attempts"] >= 3:
            raise ProviderError("Three processing attempts used; review this run before starting another")
        run = self.stage(run_id, "extracting", attempts=run["attempts"] + 1, error=None)
        if run["graph"] is None:
            self.count_call(run_id)
            graph, telemetry = self.meta.extract(self.store.image_path(run_id).read_bytes(), run["context"])
            run = self.stage(run_id, "retrieving", graph=graph.model_dump(), original_graph=graph.model_dump(),
                             telemetry=[*run["telemetry"], {"stage": "extraction", **telemetry}])
        else:
            graph = SketchGraph.model_validate(run["graph"])
            run = self.stage(run_id, "retrieving")
        if run["candidate_manifest"] is None:
            candidates, manifest = self.geography.retrieve(Area.model_validate(run["area"]))
            # Validate injected/custom adapters at the boundary too.
            area = Area.model_validate(run["area"])
            if len({c.id for c in candidates}) != len(candidates) or any(not area.contains(c.longitude, c.latitude) for c in candidates):
                raise ProviderError("Candidate data has duplicate IDs or coordinates outside the selected area")
            shortlist = shortlist_candidates(graph, candidates)
            run = self.stage(run_id, "matching", candidates=[c.model_dump() for c in candidates],
                             candidate_manifest=manifest, shortlist=shortlist)
        else:
            run = self.stage(run_id, "matching")
        if graph.landmarks and any(run["shortlist"].values()):
            self.count_call(run_id)
            proposals, telemetry = self.meta.match(graph, run["shortlist"])
            validate_proposals(graph, proposals, run["shortlist"])
            matches = [p.model_dump() for p in proposals.matches]
            history = [*run["telemetry"], {"stage": "matching", **telemetry}]
        else:
            matches = [{"sketch_id": p.id, "candidate_id": None, "evidence": "No candidate found in this area; review coverage or correct the label."} for p in graph.landmarks]
            history = run["telemetry"]
        self.stage(run_id, "needs_confirmation", proposals=matches, telemetry=history,
                   alignment={"status": "insufficient_anchors", "required": 3, "accepted": 0, "independent_error_m": None})


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
