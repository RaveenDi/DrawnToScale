from pathlib import Path

from fastapi.testclient import TestClient
import pytest

from app.main import create_app
from test_workflow import await_run

ROOT = Path(__file__).resolve().parents[3]
AREA = '{"west":79.823,"south":7.199,"east":79.848,"north":7.222}'


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("MODEL_PROVIDER", "mock")
    monkeypatch.setenv("MODEL_API_KEY", "")
    with TestClient(create_app(tmp_path)) as c:
        yield c


def run_demo(client):
    png = (ROOT / "evals/fixtures/demo-sketch.png").read_bytes()
    r = client.post("/api/runs", files={"image": ("demo.png", png, "image/png")}, data={"area_json": AREA})
    assert r.status_code == 202, r.text
    return await_run(client, r.json())


def test_mock_pipeline_end_to_end_with_verifier_rematch(client):
    assert client.get("/api/health").json()["provider"] == "mock"
    url, headers, run = run_demo(client)
    assert run["status"] == "needs_confirmation"
    assert len(run["graph"]["landmarks"]) == 5
    allowed = {c["id"] for c in run["candidates"]}
    assert all(p["candidate_id"] in allowed for p in run["proposals"] if p["candidate_id"])
    # extract + match + one bounded re-match
    assert run["model_calls"] == 3
    # mock proposals land on the intended OSM features after the verifier-triggered re-match
    import json
    intended = json.loads((ROOT / "evals/fixtures/demo-sketch.json").read_text())["intended_osm_ids"]
    got = {p["sketch_id"]: p["candidate_id"] for p in run["proposals"]}
    assert got == intended
    decisions = [{"sketch_id": k, "candidate_id": v, "note": "ok"} for k, v in got.items()]
    done = client.patch(url + "/confirmations", headers=headers, json={"revision": run["revision"], "decisions": decisions}).json()
    assert done["status"] == "ready" and done["alignment"]["status"] == "fitted"
    assert len(client.get(url + "/export", headers=headers).json()["features"]) == 5


def test_trace_steps(client):
    url, headers, run = run_demo(client)
    steps = client.get(url + "/trace", headers=headers).json()["steps"]
    assert [s["agent"] for s in steps] == ["SketchReader", "GeoRetriever", "Matcher", "Verifier", "Matcher", "Verifier", "HumanReview"]
    for s in steps:
        assert {"agent", "role", "model", "input_summary", "output_summary", "duration_ms", "status"} <= set(s)
    assert steps[3]["status"] == "rejected" and steps[5]["status"] == "ok"
    assert steps[1]["model"].startswith("none") and steps[3]["model"].startswith("none")
    assert steps[-1]["status"] == "awaiting_human"
    assert client.get(url + "/trace").status_code == 401
