"""Pure scoring for the evaluation harness. Ground truth only ever enters here, after a run finished;
it is never passed to a provider, workflow or prompt."""
from difflib import SequenceMatcher
from statistics import mean, median

from pyproj import Geod

from app.providers import normalized_name

LABEL_MATCH = 0.8  # similarity needed to call an extracted label "the same landmark" as a ground-truth label
GEOD = Geod(ellps="WGS84")


def canon(osm_id):
    """Manifest ids may be written `way/123` or `osm:way/123`; candidates use the latter."""
    return None if osm_id is None else (osm_id if osm_id.startswith("osm:") else "osm:" + osm_id)


def align_labels(gt_labels, extracted):
    """Greedy one-to-one alignment of ground-truth labels to extracted landmarks by label similarity."""
    pairs = []
    for g, label in enumerate(gt_labels):
        for e, mark in enumerate(extracted):
            a, b = normalized_name(label), normalized_name(mark["label"])
            ratio = 1.0 if a == b else SequenceMatcher(None, a, b).ratio()
            if ratio >= LABEL_MATCH:
                pairs.append((ratio, g, e))
    used_g, used_e, found = set(), set(), {}
    for _, g, e in sorted(pairs, key=lambda x: (-x[0], x[1], x[2])):
        if g not in used_g and e not in used_e:
            used_g.add(g), used_e.add(e)
            found[gt_labels[g]] = extracted[e]["id"]
    return found


def distance_m(candidates, a, b):
    if a not in candidates or b not in candidates:
        return None
    return round(GEOD.inv(*candidates[a], *candidates[b])[2], 1)


def score_item(ground_truth, graph, proposals, candidates):
    """ground_truth {label: osm_id|None}; graph/proposals/candidates from the finished run.
    candidates: {id: (lon, lat)}. Outcomes are per ground-truth label."""
    extracted = (graph or {}).get("landmarks", [])
    gt = {label: canon(osm) for label, osm in ground_truth.items()}
    found = align_labels(list(gt), extracted)
    proposed = {p["sketch_id"]: p["candidate_id"] for p in proposals}
    rows = []
    for label, truth in gt.items():
        sketch_id = found.get(label)
        pick = proposed.get(sketch_id) if sketch_id else None
        if sketch_id is None:
            outcome = "not_extracted"
        elif truth is None:
            outcome = "correct_abstain" if pick is None else "false_match"
        elif pick is None:
            outcome = "false_abstain"
        else:
            outcome = "correct" if pick == truth else "wrong_match"
        error = distance_m(candidates, pick, truth) if truth and pick else None
        rows.append({"label": label, "truth": truth, "sketch_id": sketch_id, "proposed": pick, "outcome": outcome,
                     "geo_error_m": error, "truth_in_candidates": (truth in candidates) if truth else None})
    count = lambda name: sum(1 for r in rows if r["outcome"] == name)
    with_id = sum(1 for t in gt.values() if t)
    null_extracted = sum(1 for r in rows if r["truth"] is None and r["outcome"] != "not_extracted")
    hit = len(found)
    return {"rows": rows, "extracted": len(extracted), "ground_truth": len(gt), "extraction_hits": hit,
            "extraction_recall": hit / len(gt) if gt else None, "extraction_precision": hit / len(extracted) if extracted else None,
            "gt_with_id": with_id, "correct": count("correct"), "wrong_match": count("wrong_match"), "false_abstain": count("false_abstain"),
            "gt_null_extracted": null_extracted, "correct_abstain": count("correct_abstain"), "false_match": count("false_match"),
            "not_extracted": count("not_extracted"), "geo_errors_m": [r["geo_error_m"] for r in rows if r["geo_error_m"] is not None],
            "truth_missing_from_candidates": sum(1 for r in rows if r["truth_in_candidates"] is False)}


def ratio(n, d):
    return None if not d else round(n / d, 4)


def aggregate(scores):
    """Micro-average across items."""
    s = lambda key: sum(x[key] for x in scores)
    errors = sorted(e for x in scores for e in x["geo_errors_m"])
    p90 = errors[min(len(errors) - 1, int(0.9 * len(errors)))] if errors else None
    return {"items": len(scores), "ground_truth_landmarks": s("ground_truth"), "extracted_landmarks": s("extracted"),
            "extraction_recall": ratio(s("extraction_hits"), s("ground_truth")), "extraction_precision": ratio(s("extraction_hits"), s("extracted")),
            "match_accuracy": ratio(s("correct"), s("gt_with_id")),
            "wrong_match_rate": ratio(s("wrong_match"), s("gt_with_id")), "false_abstain_rate": ratio(s("false_abstain"), s("gt_with_id")),
            "abstain_correctness": ratio(s("correct_abstain"), s("gt_null_extracted")), "false_match_on_unknown": ratio(s("false_match"), s("gt_null_extracted")),
            "not_extracted": s("not_extracted"), "truth_missing_from_candidates": s("truth_missing_from_candidates"),
            "geo_error_m": {"n": len(errors), "median": round(median(errors), 1), "mean": round(mean(errors), 1), "p90": p90, "max": errors[-1]} if errors else None}
