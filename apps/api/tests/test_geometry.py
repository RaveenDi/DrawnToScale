import numpy as np
import pytest

from app.geometry import apply_transform, fit_similarity, robust_fit


def test_rotation_outlier_and_unused_reference():
    source = np.array([[0, 0], [300, 100], [100, 400], [500, 600], [700, 200]], float)
    truth = [2.0, 0.7, 100, -30]
    target = apply_transform(source, truth)
    target[-1] += [700, -800]
    fitted, inliers, errors = robust_fit(source, target, threshold=0.01)
    assert inliers.tolist() == [True, True, True, True, False]
    unused = [[230, 570]]
    assert np.linalg.norm(apply_transform(unused, fitted) - apply_transform(unused, truth)) < 1e-8
    assert errors[-1] > 100


@pytest.mark.parametrize("source,target", [
    ([[0, 0], [1, 1]], [[0, 0], [1, 1]]),
    ([[0, 0], [1, 0], [2, 0]], [[0, 0], [1, 0], [2, 0]]),
    ([[0, 0], [1, 0], [2, 1e-7]], [[0, 0], [1, 0], [2, 1e-7]]),
    ([[0, 0], [1, 0], [1, 1]], [[0, 0], [1, 0], [float("nan"), 1]]),
])
def test_degenerate_anchor_sets(source, target):
    with pytest.raises(ValueError):
        fit_similarity(source, target)


def test_reflection_is_not_silently_fit():
    source = np.array([[0, 0], [300, 100], [100, 400], [500, 600], [700, 200]], float)
    with pytest.raises(ValueError):
        robust_fit(source, source * [-1, 1], threshold=0.001)
