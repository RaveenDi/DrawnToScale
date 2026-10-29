"""Synthetic correspondence checks only. No model, OSM or real sketch input."""
import itertools
import json
import numpy as np


def fit_similarity(source, target):
    source, target = np.asarray(source, float), np.asarray(target, float)
    if source.shape != target.shape or source.ndim != 2 or source.shape[1] != 2:
        raise ValueError("Expected corresponding Nx2 arrays")
    if len(source) < 3 or not np.isfinite(source).all() or not np.isfinite(target).all():
        raise ValueError("Need at least three finite anchors")
    if min(np.linalg.matrix_rank(a - a.mean(axis=0)) for a in (source, target)) < 2:
        raise ValueError("Product gate rejects collinear/degenerate anchors")
    rows, values = [], []
    for (x, y), (u, v) in zip(source, target):
        rows.extend([[x, -y, 1, 0], [y, x, 0, 1]])
        values.extend([u, v])
    params = np.linalg.lstsq(np.asarray(rows), np.asarray(values), rcond=None)[0]
    if np.hypot(params[0], params[1]) < 1e-9:
        raise ValueError("Degenerate scale")
    return params


def transform(points, params):
    a, b, tx, ty = params
    return np.asarray(points) @ np.array([[a, b], [-b, a]]) + [tx, ty]


def robust_fit(source, target, threshold):
    source, target = np.asarray(source, float), np.asarray(target, float)
    best = None
    for sample in itertools.combinations(range(len(source)), 3):
        try:
            params = fit_similarity(source[list(sample)], target[list(sample)])
        except ValueError:
            continue
        errors = np.linalg.norm(transform(source, params) - target, axis=1)
        inliers = errors <= threshold
        count = int(inliers.sum())
        if count < 3:
            continue
        quality = (count, -float(np.median(errors[inliers])))
        if best is None or quality > best[0]:
            best = (quality, inliers)
    if best is None:
        raise ValueError("Insufficient mutually consistent anchors")
    inliers = best[1]
    params = fit_similarity(source[inliers], target[inliers])
    if np.any(np.linalg.norm(transform(source[inliers], params) - target[inliers], axis=1) > threshold):
        raise ValueError("Refit fails declared inlier tolerance")
    return params, inliers


def rejected(fn):
    try:
        fn()
    except ValueError:
        return True
    return False


def main():
    source = np.array([[0, 0], [3, 1], [1, 4], [5, 6], [7, 2]], float)
    truth = np.array([2.0, 0.7, 100.0, -30.0])
    target = transform(source, truth)
    corrupted = target.copy()
    corrupted[-1] += [70, -80]
    fitted, inliers = robust_fit(source, corrupted, 0.01)
    holdout = np.array([[2.3, 5.7]])  # not used to fit
    holdout_error = float(np.linalg.norm(transform(holdout, fitted) - transform(holdout, truth)))
    checks = {
        "rotation_scale_translation_recovered": bool(np.allclose(fit_similarity(source, target), truth)),
        "one_bad_correspondence_rejected": inliers.tolist() == [True, True, True, True, False],
        "unused_point_error_below_1e-8_synthetic_units": holdout_error < 1e-8,
        "too_few_anchors_rejected": rejected(lambda: fit_similarity(source[:2], target[:2])),
        "collinear_anchors_rejected": rejected(lambda: fit_similarity([[0, 0], [1, 0], [2, 0]], [[0, 1], [2, 1], [4, 1]])),
        "reflection_not_silently_fit": rejected(lambda: robust_fit(source, source * [-1, 1], 0.000001)),
        "nonfinite_coordinates_rejected": rejected(lambda: fit_similarity(source[:3], [[0, 0], [1, 1], [float('nan'), 2]])),
    }
    print(json.dumps({"kind": "synthetic_geometry_only", "checks": checks,
                      "passed": sum(checks.values()), "total": len(checks),
                      "model_tested": False, "holdout_error_synthetic_units": holdout_error}, indent=2))
    return 0 if all(checks.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
