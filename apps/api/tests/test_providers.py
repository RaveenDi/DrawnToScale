from copy import deepcopy
from io import BytesIO
import json

import httpx
import pytest
from PIL import Image

from app.contracts import Proposals, SketchGraph
from app.providers import (MetaProvider, OSMProvider, ProviderError, prepare_image,
                           shortlist_candidates, validate_proposals)


def test_actual_http_request_shape_with_mock_transport(graph, image_bytes):
    seen = []
    def respond(request):
        seen.append(request)
        body = json.loads(request.content)
        assert str(request.url) == "https://api.meta.ai/v1/chat/completions"
        assert request.headers["authorization"] == "Bearer test-only-placeholder"
        assert body["max_completion_tokens"] == 4096
        assert body["response_format"]["json_schema"]["strict"] is True
        assert body["messages"][1]["content"][1]["image_url"]["url"].startswith("data:image/png;base64,")
        return httpx.Response(200, json={"model": "test-double", "id": "test",
            "choices": [{"finish_reason": "stop", "message": {"content": graph.model_dump_json()}}],
            "usage": {"total_tokens": 5}})
    provider = MetaProvider("test-only-placeholder", client=httpx.Client(transport=httpx.MockTransport(respond)))
    extracted, telemetry = provider.extract(image_bytes, "test")
    assert extracted == graph and len(seen) == 1
    assert telemetry["usage"] == {"total_tokens": 5}
    assert "test-only-placeholder" not in json.dumps(telemetry)


@pytest.mark.parametrize("kind", ["http", "timeout", "redirect", "malformed", "truncated", "invalid_graph"])
def test_model_failures_are_safe_and_not_retried(kind, image_bytes):
    calls = []
    def respond(request):
        calls.append(request)
        if kind == "timeout":
            raise httpx.ReadTimeout("SECRET EXCEPTION TEXT", request=request)
        if kind == "http":
            return httpx.Response(401, text="SECRET RESPONSE BODY")
        if kind == "redirect":
            return httpx.Response(302, headers={"location": "https://unrelated.example"})
        if kind == "malformed":
            return httpx.Response(200, text="not json")
        return httpx.Response(200, json={"choices": [{"finish_reason": "length" if kind == "truncated" else "stop",
                                                     "message": {"content": "{}"}}]})
    provider = MetaProvider("test-placeholder", client=httpx.Client(transport=httpx.MockTransport(respond)))
    with pytest.raises(ProviderError) as error:
        provider.extract(image_bytes, "")
    assert "SECRET" not in str(error.value)
    assert len(calls) == 1


def test_unknown_candidate_ids_and_missing_sketch_ids_rejected(graph, candidates):
    shortlist = shortlist_candidates(graph, candidates)
    valid = [{"sketch_id": p.id, "candidate_id": None, "evidence": "uncertain"} for p in graph.landmarks]
    validate_proposals(graph, Proposals(matches=valid), shortlist)
    bad = deepcopy(valid)
    bad[0]["candidate_id"] = "invented:123"
    with pytest.raises(ProviderError):
        validate_proposals(graph, Proposals(matches=bad), shortlist)
    with pytest.raises(ProviderError):
        validate_proposals(graph, Proposals(matches=valid[:-1]), shortlist)


@pytest.mark.parametrize("mutation", ["duplicate", "missing_relation", "compass", "bool", "nonfinite", "extra"])
def test_extraction_semantic_boundaries(graph, mutation):
    data = graph.model_dump()
    if mutation == "duplicate":
        data["landmarks"][1]["id"] = "p1"
    elif mutation == "missing_relation":
        data["relations"] = [{"from_id": "p1", "to_id": "missing", "kind": "near", "evidence": "test"}]
    elif mutation == "compass":
        data["relations"] = [{"from_id": "p1", "to_id": "p2", "kind": "north_of", "evidence": "test"}]
    elif mutation == "bool":
        data["landmarks"][0]["x"] = True
    elif mutation == "nonfinite":
        data["landmarks"][0]["x"] = float("nan")
    else:
        data["latitude"] = 7.0
    with pytest.raises(ValueError):
        SketchGraph.model_validate(data)


def test_image_orientation_and_metadata_are_normalized():
    image = Image.new("RGB", (40, 20), "white")
    exif = Image.Exif()
    exif[274] = 6
    exif[315] = "Private author"
    raw = BytesIO()
    image.save(raw, format="JPEG", exif=exif)
    normalized, info = prepare_image(raw.getvalue())
    assert (info["width"], info["height"]) == (20, 40)
    with Image.open(BytesIO(normalized)) as result:
        assert result.format == "PNG" and not result.getexif()
    with pytest.raises(ValueError):
        prepare_image(b"\x89PNG\r\n\x1a\nnot really an image")


def test_osm_bounding_cache_centers_and_attribution(tmp_path, area):
    requests = []
    def respond(request):
        requests.append(request)
        assert b"nwr" in request.content
        return httpx.Response(200, json={"elements": [
            {"type": "node", "id": 1, "lat": 6.91, "lon": 79.85, "tags": {"name": "Library", "name:en": "Library", "amenity": "library"}},
            {"type": "way", "id": 2, "center": {"lat": 6.92, "lon": 79.86}, "tags": {"name": "Park", "leisure": "park"}},
            {"type": "node", "id": 3, "lat": 9.0, "lon": 80.0, "tags": {"name": "Outside"}},
        ]})
    provider = OSMProvider(tmp_path, client=httpx.Client(transport=httpx.MockTransport(respond)))
    candidates, manifest = provider.retrieve(area)
    assert len(candidates) == 2
    assert candidates[1].position_kind == "bounding_box_center"
    assert candidates[0].source_url == "https://www.openstreetmap.org/node/1"
    assert manifest["attribution"] == "© OpenStreetMap contributors"
    again, cached = provider.retrieve(area)
    assert candidates == again and cached["cached"] and len(requests) == 1
    assert cached["retrieved_at"] == manifest["retrieved_at"]


def test_osm_partial_response_is_not_coverage(tmp_path, area):
    client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200,
        json={"elements": [], "remark": "runtime error: timeout"})))
    with pytest.raises(ProviderError):
        OSMProvider(tmp_path, client).retrieve(area)
    assert not list(tmp_path.glob("*.json"))
