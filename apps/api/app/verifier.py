"""Deterministic Verifier: geometry only, no model. Checks that proposed matches are mutually
consistent with a similarity transform (sketch -> metres), flags outliers and reports
leave-one-out (held-out) error. Fit residuals are not independent accuracy."""
import numpy as np
from pyproj import CRS, Transformer

from .geometry import apply_transform, fit_similarity, robust_fit

TOLERANCE_M = 50.0


def verify(graph, proposals, candidates, area, image):
    cand = {c["id"]: c for c in candidates}
    marks = {p["id"]: p for p in graph["landmarks"]}
    picked = [(p["sketch_id"], p["candidate_id"]) for p in proposals if p["candidate_id"] in cand]
    if len(picked) < 3:
        return {"status": "insufficient_anchors", "anchors": len(picked), "outliers": [], "held_out_error_m": None}
    local = CRS.from_proj4(f"+proj=aeqd +lat_0={(area['south'] + area['north']) / 2} "
                           f"+lon_0={(area['west'] + area['east']) / 2} +datum=WGS84 +units=m")
    proj = Transformer.from_crs("EPSG:4326", local, always_xy=True)
    ids = [s for s, _ in picked]
    src = np.array([[marks[s]["x"] * image["width"], -marks[s]["y"] * image["height"]] for s in ids])
    dst = np.array([proj.transform(cand[c]["longitude"], cand[c]["latitude"]) for _, c in picked])
    try:
        params, inliers, errors = robust_fit(src, dst, TOLERANCE_M)
    except ValueError as exc:
        return {"status": "rejected", "reason": str(exc), "anchors": len(ids), "outliers": ids, "held_out_error_m": None}
    outliers = [s for s, ok in zip(ids, inliers) if not ok]
    held = []
    for i in range(len(ids)):
        rest = [j for j in range(len(ids)) if j != i]
        if len(rest) < 3:
            break
        try:
            p = fit_similarity(src[rest], dst[rest])
        except ValueError:
            continue
        held.append(float(np.linalg.norm(apply_transform(src[i:i + 1], p)[0] - dst[i])))
    return {"status": "rejected" if outliers else "pass", "anchors": len(ids), "outliers": outliers,
            "tolerance_m": TOLERANCE_M, "fit_residuals_m": {s: round(float(e), 1) for s, e in zip(ids, errors)},
            "held_out_error_m": {"median": round(float(np.median(held)), 1), "max": round(max(held), 1)} if held else None,
            "note": "Self-consistency of proposals, not ground-truth accuracy."}
