import time

from fastapi.testclient import TestClient
import pytest

from app.contracts import Area, Proposals
from app.main import create_app
from app.providers import ProviderError
from app.storage import Store


def create_run(client, image_bytes, area):
    response = client.post("/api/runs", files={"image": ("sketch.png", image_bytes, "image/png")},
                           data={"area_json": area.model_dump_json()})
    assert response.status_code == 202, response.text
    return response.json()


def await_run(client, created):
    headers = {"Authorization": "Bearer " + created["access_token"]}
    url = "/api/runs/" + created["id"]
    for _ in range(200):
        response = client.get(url, headers=headers)
        assert response.status_code == 200
        result = response.json()
        if result["status"] in {"needs_confirmation", "ready", "failed", "interrupted"}:
            return url, headers, result
        time.sleep(0.01)
    raise AssertionError("Local workflow did not finish")


def test_upload_review_correction_export_and_delete(tmp_path, providers, image_bytes, area):
    meta, geography = providers
    with TestClient(create_app(tmp_path, meta, geography)) as client:
        created = create_run(client, image_bytes, area)
        url, headers, run = await_run(client, created)
        assert run["status"] == "needs_confirmation"
        assert meta.extract_calls == meta.match_calls == geography.calls == 1
        assert client.get(url).status_code == 401
        assert client.get(url, headers={"Authorization": "Bearer wrong"}).status_code == 404
        assert client.get(url + "/image", headers=headers).content.startswith(b"\x89PNG")
        assert client.get(url + "/export", headers=headers).json()["features"] == []
        invalid = {"revision": run["revision"], "decisions": [{"sketch_id": "p1", "candidate_id": "invented", "note": ""}]}
        assert client.patch(url + "/confirmations", headers=headers, json=invalid).status_code == 422
        decisions = [{"sketch_id": f"p{i+1}", "candidate_id": f"synthetic:{i}" if i < 3 else None, "note": "Test confirmation"} for i in range(4)]
        patch = {"revision": run["revision"], "decisions": decisions}
        confirmed = client.patch(url + "/confirmations", headers=headers, json=patch)
        assert confirmed.status_code == 200, confirmed.text
        run = confirmed.json()
        assert run["status"] == "ready"
        assert run["alignment"]["status"] == "fitted"
        assert max(a["fit_residual_m"] for a in run["alignment"]["anchors"]) < 0.001
        assert run["alignment"]["independent_error_m"] is None
        assert client.patch(url + "/confirmations", headers=headers, json=patch).status_code == 409
        exported = client.get(url + "/export", headers=headers).json()
        assert len(exported["features"]) == 3
        assert exported["metadata"]["unresolved_landmarks"][0]["status"] == "unmatched"
        assert exported["features"][0]["geometry"]["coordinates"] == [geography.candidates[0].longitude, geography.candidates[0].latitude]
        assert exported["features"][0]["properties"]["position_kind"] == "synthetic"
        assert "NOT MEASURED" in client.get(url + "/report", headers=headers).text
        original = run["original_graph"]
        edited = run["graph"]
        edited["landmarks"][0]["x"] = 0.2
        response = client.patch(url + "/graph", headers=headers, json={"revision": run["revision"], "graph": edited, "note": "Correct position"})
        assert response.status_code == 200
        corrected = response.json()
        assert corrected["original_graph"] == original
        assert corrected["confirmations"] == {} and corrected["alignment"] is None
        assert client.get(url + "/export", headers=headers).json()["features"] == []
        assert meta.extract_calls == meta.match_calls == 1
        assert client.delete(url, headers=headers).status_code == 204
        assert client.get(url, headers=headers).status_code == 404
        assert not (tmp_path / "images" / f"{created['id']}.png").exists()


def test_failure_is_saved_and_retry_reuses_successful_extraction(tmp_path, providers, image_bytes, area):
    meta, geography = providers
    original = geography.retrieve
    def fail_once(area):
        geography.retrieve = original
        raise ProviderError("Synthetic timeout")
    geography.retrieve = fail_once
    with TestClient(create_app(tmp_path, meta, geography)) as client:
        created = create_run(client, image_bytes, area)
        url, headers, run = await_run(client, created)
        assert run["status"] == "failed" and run["original_graph"]
        assert client.get(url + "/export", headers=headers).status_code == 409
        assert client.post(url + "/retry", headers=headers).status_code == 202
        _, _, run = await_run(client, created)
        assert run["status"] == "needs_confirmation"
        assert meta.extract_calls == 1
        assert run["attempts"] == 2 and run["model_calls"] == 2


def test_interrupted_run_recovery(tmp_path, image_bytes, area):
    store = Store(tmp_path)
    run, token = store.create(area, image_bytes, {"width": 800, "height": 400}, "")
    store.recover()
    recovered = store.get(run["id"], token)
    assert recovered["status"] == "interrupted"
    assert recovered["events"][-1]["kind"] == "interrupted"


def test_local_boundaries_and_no_key(tmp_path, providers, image_bytes, area):
    meta, geography = providers
    meta.configured = False
    with TestClient(create_app(tmp_path, meta, geography)) as client:
        response = client.post("/api/runs", files={"image": ("test.png", image_bytes)}, data={"area_json": area.model_dump_json()})
        assert response.status_code == 503
        assert meta.extract_calls == 0
        assert client.get("/api/health", headers={"Origin": "https://unrelated.example"}).status_code == 403
        assert client.get("/api/health", headers={"Host": "attacker.example"}).status_code == 400
        assert client.post("/api/runs", headers={"Content-Length": str(10*1024*1024)}).status_code == 413


def test_duplicate_candidate_confirmation_rejected(tmp_path, providers, image_bytes, area):
    with TestClient(create_app(tmp_path, *providers)) as client:
        created = create_run(client, image_bytes, area)
        url, headers, run = await_run(client, created)
        response = client.patch(url + "/confirmations", headers=headers, json={"revision": run["revision"], "decisions": [
            {"sketch_id": "p1", "candidate_id": "synthetic:0", "note": ""},
            {"sketch_id": "p2", "candidate_id": "synthetic:0", "note": ""}]})
        assert response.status_code == 422
        assert client.get(url, headers=headers).json()["confirmations"] == {}


def test_worker_capacity_rejects_new_run_without_saving_it(tmp_path, providers, image_bytes, area):
    app = create_app(tmp_path, *providers)
    with TestClient(app) as client:
        app.state.workflow.reserve()
        try:
            response = client.post("/api/runs", files={"image": ("sketch.png", image_bytes, "image/png")},
                                   data={"area_json": area.model_dump_json()})
            assert response.status_code == 409
            assert not list((tmp_path / "images").glob("*.png"))
        finally:
            app.state.workflow.slot.release()


def test_invalid_model_matching_is_saved_as_failure_and_bounded(tmp_path, providers, image_bytes, area):
    meta, geography = providers
    def invent(graph, shortlist):
        meta.match_calls += 1
        return Proposals(matches=[{"sketch_id": p.id, "candidate_id": "invented", "evidence": "invalid"}
                                  for p in graph.landmarks]), {"provider": "synthetic_test_double"}
    meta.match = invent
    with TestClient(create_app(tmp_path, meta, geography)) as client:
        created = create_run(client, image_bytes, area)
        url, headers, run = await_run(client, created)
        assert run["status"] == "failed" and run["model_calls"] == 2
        assert run["proposals"] == []
        for _ in range(2):
            assert client.post(url + "/retry", headers=headers).status_code == 202
            _, _, run = await_run(client, created)
            assert run["status"] == "failed"
        assert meta.extract_calls == 1 and meta.match_calls == 3
        assert run["model_calls"] == 4
        assert client.post(url + "/retry", headers=headers).status_code == 409


def test_empty_geographic_coverage_abstains_without_model_matching(tmp_path, providers, image_bytes, area):
    meta, geography = providers
    geography.candidates = []
    with TestClient(create_app(tmp_path, meta, geography)) as client:
        created = create_run(client, image_bytes, area)
        _, _, run = await_run(client, created)
        assert run["status"] == "needs_confirmation"
        assert all(p["candidate_id"] is None for p in run["proposals"])
        assert meta.match_calls == 0 and run["model_calls"] == 1


@pytest.mark.parametrize("bbox", [
    {"west": 79.9, "east": 79.8, "south": 6.9, "north": 7.0},
    {"west": 79.0, "east": 81.0, "south": 6.0, "north": 8.0},
    {"west": 79.84, "east": 79.85, "south": float("nan"), "north": 6.9},
])
def test_invalid_areas_rejected(bbox):
    with pytest.raises(ValueError):
        Area.model_validate(bbox)
