from io import BytesIO

from PIL import Image
import pytest
from pyproj import CRS, Transformer

from app.contracts import Area, Candidate, Proposals, SketchGraph


@pytest.fixture
def area():
    return Area(west=79.84, south=6.89, east=79.87, north=6.93)


@pytest.fixture
def image_bytes():
    output = BytesIO()
    Image.new("RGB", (800, 400), "white").save(output, format="PNG")
    return output.getvalue()


@pytest.fixture
def graph():
    return SketchGraph(north_direction="unknown", landmarks=[
        {"id": "p1", "label": "Sample Library", "type": "library", "x": 0.1, "y": 0.2, "evidence": "Synthetic label"},
        {"id": "p2", "label": "Sample School", "type": "school", "x": 0.8, "y": 0.3, "evidence": "Synthetic label"},
        {"id": "p3", "label": "Sample Park", "type": "park", "x": 0.3, "y": 0.9, "evidence": "Synthetic label"},
        {"id": "p4", "label": "Unmapped stall", "type": "shop", "x": 0.6, "y": 0.6, "evidence": "Synthetic label"},
    ], relations=[], unresolved=["Synthetic fixtures are not real sketch-reading evidence."])


@pytest.fixture
def candidates(graph):
    local = CRS.from_proj4("+proj=aeqd +lat_0=6.91 +lon_0=79.855 +datum=WGS84 +units=m")
    inverse = Transformer.from_crs(local, "EPSG:4326", always_xy=True)
    result = []
    for i, p in enumerate(graph.landmarks[:3]):
        lon, lat = inverse.transform(p.x * 800 - 400, -p.y * 400 + 200)
        result.append(Candidate(id=f"synthetic:{i}", label=p.label, aliases=[], type=p.type,
                                longitude=lon, latitude=lat, source_url="synthetic:test-fixture",
                                position_kind="synthetic"))
    return result


class FakeMeta:
    configured = True

    def __init__(self, graph):
        self.graph = graph
        self.extract_calls = self.match_calls = 0

    def extract(self, image, context):
        self.extract_calls += 1
        return self.graph, {"provider": "synthetic_test_double", "live": False}

    def match(self, graph, shortlist):
        self.match_calls += 1
        return Proposals(matches=[{"sketch_id": p.id, "candidate_id": f"synthetic:{i}" if i < 3 else None,
                                   "evidence": "Synthetic proposal, not an actual model response"}
                                  for i, p in enumerate(graph.landmarks)]), {"provider": "synthetic_test_double", "live": False}


class FakeGeography:
    def __init__(self, candidates):
        self.candidates, self.calls = candidates, 0

    def retrieve(self, area):
        self.calls += 1
        return self.candidates, {"provider": "synthetic_test_double", "attribution": "Synthetic test fixture",
                                 "license_url": "", "retrieved_at": "2026-10-29T00:00:00+00:00",
                                 "candidate_count": len(self.candidates)}


@pytest.fixture
def providers(graph, candidates):
    return FakeMeta(graph), FakeGeography(candidates)
