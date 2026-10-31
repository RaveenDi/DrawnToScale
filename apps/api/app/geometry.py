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


def scaled_tolerance(target, k=None):
    """Metres; one scale policy for verification, provisional and confirmed fits."""
    from .config import Settings
    k = Settings.from_env().geometry_tolerance_k if k is None else k
    if not np.isfinite(k) or k < 0:
        raise ValueError("Geometry tolerance factor must be finite and nonnegative")
    distances = [np.linalg.norm(np.asarray(a) - b) for a, b in itertools.combinations(target, 2)]
    return max(50.0, k * float(np.median(distances))) if distances else 50.0


def anchor_reason(source, target):
    """Check orientation using the majority of nondegenerate corresponding triangles.

    A single distorted/outlier triangle must not label the whole layout reflected.
    With three anchors this is exactly the signed triangle orientation test.
    """
    source, target = np.asarray(source, float), np.asarray(target, float)
    if len(source) < 3:
        return "too_few"
    for points in (source, target):
        singular = np.linalg.svd(points - points.mean(axis=0), compute_uv=False)
        if singular[0] < 1e-9 or singular[1] / singular[0] < 0.01:
            return "collinear"
    signs = []
    for sample in itertools.combinations(range(len(source)), 3):
        areas = []
        for points in (source, target):
            a, b, c = points[list(sample)]
            u, v = b - a, c - a
            areas.append(u[0] * v[1] - u[1] * v[0])
        if all(abs(a) > 1e-9 for a in areas):
            signs.append(np.sign(areas[0]) == np.sign(areas[1]))
    if signs and sum(signs) < len(signs) / 2:
        return "reflected"
    return None


def robust_fit(source, target, threshold=None):
    source, target = np.asarray(source, float), np.asarray(target, float)
    threshold = scaled_tolerance(target) if threshold is None else threshold
    reason = anchor_reason(source, target)
    if reason:
        raise ValueError(reason)
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
        params = fit_similarity(source, target)
        errors = np.linalg.norm(apply_transform(source, params) - target, axis=1)
        return params, errors <= threshold, errors
    inliers = best[1]
    params = fit_similarity(source[inliers], target[inliers])
    errors = np.linalg.norm(apply_transform(source, params) - target, axis=1)
    return params, errors <= threshold, errors


def align(run):
    accepted = {k: v["candidate_id"] for k, v in run["confirmations"].items() if v["candidate_id"]}
    if len(accepted) < 3:
        return {"status": "insufficient_anchors", "reason": "too_few", "required": 3, "accepted": len(accepted),
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
            "params": params.tolist(), "crs": local.to_string(), "tolerance_m": scaled_tolerance(target),
            "reason": None if bool(inliers.all()) else "loose_fit",
            "anchors": [{"sketch_id": i, "inlier": bool(ok), "fit_residual_m": round(float(error), 3)}
                        for i, ok, error in zip(ids, inliers, errors)],
            "independent_error_m": None,
            "note": "Fit residuals are not independent location accuracy. No holdout supplied."}
