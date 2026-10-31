"""Synthetic geometry checks only; not model accuracy evidence."""
import numpy as np
import pytest
from pyproj import CRS, Transformer

from app.config import Settings
from app.geometry import align, scaled_tolerance
from app.verifier import verify


def layout(source, target):
    area = {"west": 79.9, "east": 80.1, "south": 6.9, "north": 7.1}
    crs = CRS.from_proj4("+proj=aeqd +lat_0=7 +lon_0=80 +datum=WGS84 +units=m")
    inverse = Transformer.from_crs(crs, "EPSG:4326", always_xy=True)
    graph = {"landmarks": [{"id": str(i), "x": x / 1000, "y": -y / 1000} for i, (x, y) in enumerate(source)]}
    candidates = []
    for i, xy in enumerate(target):
        lon, lat = inverse.transform(*xy)
        candidates.append({"id": str(i), "longitude": lon, "latitude": lat})
    proposals = [{"sketch_id": str(i), "candidate_id": str(i)} for i in range(len(source))]
    return graph, proposals, candidates, area, {"width": 1000, "height": 1000}


@pytest.mark.parametrize("target,reason,status", [
    ([[0, 0], [1000, 0], [0, 1000]], None, "pass"),
    ([[0, 0], [-1000, 0], [0, 1000]], "reflected", "rejected"),
    ([[0, 0], [1000, 0], [2000, 0]], "collinear", "rejected"),
    ([[0, 0], [1000, 0], [-1000, 200]], "loose_fit", "needs_review"),
])
def test_verdict_reasons_and_confirmed_fit(target, reason, status):
    args = layout([[0, 0], [1000, 0], [0, 1000]], target)
    result = verify(*args)
    assert result["reason"] == reason and result["status"] == status
    assert result["tolerance_m"] == pytest.approx(scaled_tolerance(target))
    graph, proposals, candidates, area, image = args
    fit = align({"graph": graph, "candidates": candidates, "area": area, "image": image,
                 "confirmations": {p["sketch_id"]: p for p in proposals}})
    assert ("params" in fit) == (status != "rejected")
    if reason == "loose_fit":
        assert len(result["fit_residuals_m"]) == 3
        assert max(result["fit_residuals_m"].values()) > result["tolerance_m"]
        assert fit["reason"] == "loose_fit"


def test_too_few_and_tolerance_floor_and_configuration(monkeypatch):
    args = layout([[0, 0], [10, 0]], [[0, 0], [10, 0]])
    result = verify(*args)
    assert result["reason"] == "too_few" and result["tolerance_m"] == 50
    monkeypatch.setenv("GEOMETRY_TOLERANCE_K", "0.2")
    assert Settings.from_env().geometry_tolerance_k == 0.2
    assert scaled_tolerance([[0, 0], [1000, 0]]) == 200
    for value in ["-1", "nan", "inf"]:
        with pytest.raises(ValueError):
            Settings.from_env({"GEOMETRY_TOLERANCE_K": value})


def test_source_collinear_and_mirror_rejected_even_at_huge_tolerance():
    args = layout([[0, 0], [1, 0], [2, 0]], [[0, 0], [1000, 0], [0, 1000]])
    assert verify(*args)["reason"] == "collinear"
    args = layout([[0, 0], [1000, 0], [0, 1000]], [[0, 0], [-1000, 0], [0, 1000]])
    assert verify(*args, k=100)["reason"] == "reflected"
