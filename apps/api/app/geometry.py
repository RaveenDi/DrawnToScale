"""Fit only accepted anchors. Image y points down; projected y points north."""
import itertools

import numpy as np
from pyproj import CRS, Transformer


def apply_transform(points, params):
    a, b, tx, ty = params
    return np.asarray(points) @ np.array([[a, b], [-b, a]]) + [tx, ty]


def fit_similarity(source, target):
    source, target = np.asarray(source, float), np.asarray(target, float)
    if source.shape != target.shape or source.ndim != 2 or source.shape[1] != 2:
        raise ValueError("Expected corresponding Nx2 anchors")
    if len(source) < 3 or not np.isfinite(source).all() or not np.isfinite(target).all():
        raise ValueError("Need at least three finite anchors")
    for points in (source, target):
        singular = np.linalg.svd(points - points.mean(axis=0), compute_uv=False)
        if singular[0] < 1e-9 or singular[1] / singular[0] < 0.01:
            raise ValueError("Anchors are collinear or too close to a line")
    # Center first to keep large projected offsets out of the least-squares fit.
    s, t = source - source.mean(axis=0), target - target.mean(axis=0)
    rows, values = [], []
    for (x, y), (u, v) in zip(s, t):
        rows.extend([[x, -y], [y, x]])
        values.extend([u, v])
    a, b = np.linalg.lstsq(rows, values, rcond=None)[0]
    if np.hypot(a, b) < 1e-9:
        raise ValueError("Degenerate scale")
    offset = target.mean(axis=0) - apply_transform([source.mean(axis=0)], [a, b, 0, 0])[0]
    return np.array([a, b, *offset])


def robust_fit(source, target, threshold=50.0):
    source, target = np.asarray(source, float), np.asarray(target, float)
    if not 3 <= len(source) <= 12 or source.shape != target.shape or threshold <= 0:
        raise ValueError("Need 3–12 corresponding anchors and a positive tolerance")
    best = None
    for sample in itertools.combinations(range(len(source)), 3):
        try:
            params = fit_similarity(source[list(sample)], target[list(sample)])
        except ValueError:
            continue
        errors = np.linalg.norm(apply_transform(source, params) - target, axis=1)
        inliers = errors <= threshold
        count = int(inliers.sum())
        if count < 3:
            continue
        quality = (count, -float(np.median(errors[inliers])))
        if best is None or quality > best[0]:
            best = quality, inliers
    if best is None:
        raise ValueError("No consistent non-reflected alignment; review your anchors")
    inliers = best[1]
    params = fit_similarity(source[inliers], target[inliers])
    errors = np.linalg.norm(apply_transform(source, params) - target, axis=1)
    if np.any(errors[inliers] > threshold):
        raise ValueError("Refitted anchors exceed the declared tolerance")
    return params, inliers, errors


def align(run):
    accepted = {k: v["candidate_id"] for k, v in run["confirmations"].items() if v["candidate_id"]}
    if len(accepted) < 3:
        return {"status": "insufficient_anchors", "required": 3, "accepted": len(accepted),
                "independent_error_m": None}
    candidates = {c["id"]: c for c in run["candidates"]}
    landmarks = {p["id"]: p for p in run["graph"]["landmarks"]}
    area = run["area"]
    local = CRS.from_proj4(f"+proj=aeqd +lat_0={(area['south'] + area['north']) / 2} "
                          f"+lon_0={(area['west'] + area['east']) / 2} +datum=WGS84 +units=m")
    projection = Transformer.from_crs("EPSG:4326", local, always_xy=True)
    width, height = run["image"]["width"], run["image"]["height"]
    ids = list(accepted)
    source = [[landmarks[i]["x"] * width, -landmarks[i]["y"] * height] for i in ids]
    target = [projection.transform(candidates[accepted[i]]["longitude"], candidates[accepted[i]]["latitude"]) for i in ids]
    try:
        params, inliers, errors = robust_fit(source, target)
    except ValueError as exc:
        return {"status": "needs_review", "reason": str(exc), "independent_error_m": None}
    return {"status": "fitted" if bool(inliers.all()) else "needs_review", "family": "similarity",
            "params": params.tolist(), "crs": local.to_string(), "tolerance_m": 50,
            "anchors": [{"sketch_id": i, "inlier": bool(ok), "fit_residual_m": round(float(error), 3)}
                        for i, ok, error in zip(ids, inliers, errors)],
            "independent_error_m": None,
            "note": "Fit residuals are not independent location accuracy. No holdout supplied."}
