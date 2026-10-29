import json
from pathlib import Path

import httpx
import pytest

from app.llm import Transport
from app.mock import FixtureGeography, MockProvider
from app.providers import MetaProvider
from conftest import FakeGeography
from evals import run as harness
from evals import score

ROOT = Path(__file__).resolve().parents[3]
DEMO = ROOT / "evals" / "manifest.demo.jsonl"
GT = {"Dutch Fort": "way/196057979", "Arpico Super Market": "way/581075989", "Maris Stella": "node/1268340441",
      "Bus Terminal": "way/97913613", "St Sebastian Church": "way/1472244684"}


def write_manifest(path, *items):
    path.write_text("\n".join(json.dumps(i) for i in items), encoding="utf-8")
    return path


def item(**over):
    base = {"id": "a", "image": str(ROOT / "evals/fixtures/demo-sketch.png"), "area": "negombo-central", "split": "dev",
            "rights": "synthetic", "ground_truth": GT}
    return {**base, **over}


def test_mock_demo_manifest_runs_end_to_end_and_writes_both_reports(tmp_path):
    report, base = harness.evaluate("mock", DEMO, "dev", out_dir=tmp_path, data_dir=tmp_path / "data")
    a = report["aggregate"]
    assert (a["extraction_recall"], a["extraction_precision"], a["match_accuracy"]) == (1.0, 1.0, 1.0)
    assert a["geo_error_m"]["max"] == 0.0 and a["telemetry"]["model_calls"] == 7 and a["telemetry"]["steps"] == 6
    assert report["meta"]["provider"] == "mock" and report["meta"]["evidence"].startswith("NONE")
    assert json.loads(base.with_suffix(".json").read_text(encoding="utf-8"))["items"][0]["id"] == "demo-synthetic-negombo"
    text = base.with_suffix(".md").read_text(encoding="utf-8")
    assert "Extraction recall" in text and "Abstain correctness" in text and "Per-landmark outcomes" in text
    assert base.name.endswith("-dev")


def test_reflected_sketch_trap_is_caught_by_abstaining_not_by_guessing(tmp_path):
    report, _ = harness.evaluate("mock", DEMO, "heldout", out_dir=tmp_path, data_dir=tmp_path / "data")
    a = report["aggregate"]
    assert report["meta"]["held_out"] is True and a["wrong_match_rate"] == 0.0 and a["false_abstain_rate"] > 0
    assert report["items"][0]["verifier_final"] in {"ok", "rejected", "skipped"}


def test_call_cap_skips_items_it_cannot_afford(tmp_path):
    manifest = write_manifest(tmp_path / "m.jsonl", item(id="one"), item(id="two"))
    report, _ = harness.evaluate("mock", manifest, "dev", max_calls=8, out_dir=tmp_path, data_dir=tmp_path / "data")
    assert [r["id"] for r in report["items"]] == ["one"] and report["meta"]["items_skipped_for_call_cap"] == ["two"]
    assert report["meta"]["calls_used"] == 7 <= 8


def test_heldout_may_only_be_reported_once_for_a_real_provider(tmp_path):
    kwargs = dict(out_dir=tmp_path, data_dir=tmp_path / "data", meta=MockProvider(), geography=FixtureGeography())
    harness.evaluate("replay", DEMO, "heldout", **kwargs)
    with pytest.raises(SystemExit, match="already reported"):
        harness.evaluate("replay", DEMO, "heldout", **kwargs)
    harness.evaluate("replay", DEMO, "heldout", allow_repeat_heldout=True, **kwargs)
    harness.evaluate("mock", DEMO, "heldout", **kwargs)  # the mock is exempt: it is plumbing, not a result


def test_live_provider_requires_an_explicit_call_cap(tmp_path):
    with pytest.raises(SystemExit, match="--max-calls"):
        harness.evaluate("meta", DEMO, "dev", out_dir=tmp_path)


@pytest.mark.parametrize("mutate,message", [
    (lambda i: i.pop("rights"), "missing rights"), (lambda i: i.update(split="train"), "split must be"),
    (lambda i: i.update(rights=" "), "rights"), (lambda i: i.update(ground_truth={}), "ground_truth"),
    (lambda i: i.update(ground_truth={"X": 5}), "OSM id string or null"), (lambda i: i.update(image="nope.png"), "image not found")])
def test_manifest_validation(tmp_path, mutate, message):
    entry = item()
    mutate(entry)
    with pytest.raises(harness.ManifestError, match=message):
        harness.load_manifest(write_manifest(tmp_path / "m.jsonl", entry))
    with pytest.raises(harness.ManifestError, match="duplicate"):
        harness.load_manifest(write_manifest(tmp_path / "d.jsonl", item(), item()))


def test_ground_truth_is_never_sent_to_the_model(tmp_path, graph, candidates, area, image_bytes):
    """Sentinel ids and strings live only in the manifest; capture every outgoing request and look for them."""
    sketch = tmp_path / "IMG-SENTINEL-NAME.png"
    sketch.write_bytes(image_bytes)
    entry = item(id="ITEM-SENTINEL-ID", image=str(sketch), rights="RIGHTS-SENTINEL", area=area.model_dump(),
                 ground_truth={"Sample Library": "node/999999991", "Sample School": "node/999999992", "Sample Park": None, "Unmapped stall": None,
                               "GT-SENTINEL-LABEL": "node/999999993"})
    manifest = write_manifest(tmp_path / "SENTINEL-MANIFEST.jsonl", entry)
    sent, queue = [], []
    def call(name, **args):
        return {"id": f"c{len(queue)}{name}{len(args)}", "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}
    turns = [[call("search_candidates", query=q) for q in ("Sample Library", "Sample School", "Sample Park")],
             [call("propose_match", sketch_id=f"p{i + 1}", candidate_id=f"synthetic:{i}" if i < 3 else None, evidence="label match") for i in range(4)] + [call("finish")]]
    def respond(request):
        sent.append(request.content.decode("utf-8"))
        body = json.loads(request.content)
        if "response_format" in body:
            message = {"content": graph.model_dump_json()}
            finish = "stop"
        else:
            message, finish = {"content": None, "tool_calls": turns[len(sent) - 2]}, "tool_calls"
        return httpx.Response(200, json={"model": "m", "usage": {"prompt_tokens": 1, "completion_tokens": 1},
                                         "choices": [{"finish_reason": finish, "message": message}]})
    transport = Transport("sk-test", client=httpx.Client(transport=httpx.MockTransport(respond)), recordings=tmp_path / "rec")
    report, _ = harness.evaluate("meta", manifest, "dev", max_calls=10, out_dir=tmp_path / "out", data_dir=tmp_path / "data",
                                 meta=MetaProvider("sk-test", transport=transport), geography=FakeGeography(candidates))
    assert len(sent) == 3  # extraction + two tool-calling turns
    blob = "\n".join(sent)
    for secret in ("ground_truth", "ITEM-SENTINEL-ID", "RIGHTS-SENTINEL", "SENTINEL-NAME", "SENTINEL-MANIFEST", "GT-SENTINEL-LABEL",
                   "999999991", "999999992", "999999993"):
        assert secret not in blob, secret
    rows = {r["label"]: r for r in report["items"][0]["score"]["rows"]}
    assert rows["Sample Library"]["outcome"] == "wrong_match" and rows["Sample Library"]["truth_in_candidates"] is False
    assert rows["Sample Park"]["outcome"] == "false_match" and rows["Unmapped stall"]["outcome"] == "correct_abstain"
    assert rows["GT-SENTINEL-LABEL"]["outcome"] == "not_extracted"
    assert report["aggregate"]["telemetry"]["input_tokens"] == 3 and report["aggregate"]["telemetry"]["tokens_reported"] is True


def test_score_outcomes_alignment_and_geographic_error():
    graph = {"landmarks": [{"id": "p1", "label": "dutch  FORT"}, {"id": "p2", "label": "Fish Market"}, {"id": "p3", "label": "Old Well"},
                           {"id": "p4", "label": "Mystery Shop"}, {"id": "p5", "label": "Extra Item"}]}
    candidates = {"osm:node/1": (79.84, 7.20), "osm:node/2": (79.85, 7.20), "osm:node/3": (79.84, 7.21)}
    gt = {"Dutch Fort": "node/1", "Fish Market": "osm:node/2", "Old Well": "osm:node/3", "Mystery Shop": None, "Unknown Hut": None, "Gone Tower": "node/9"}
    proposals = [{"sketch_id": "p1", "candidate_id": "osm:node/1"}, {"sketch_id": "p2", "candidate_id": "osm:node/3"},
                 {"sketch_id": "p3", "candidate_id": None}, {"sketch_id": "p4", "candidate_id": "osm:node/2"}, {"sketch_id": "p5", "candidate_id": None}]
    s = score.score_item(gt, graph, proposals, candidates)
    outcomes = {r["label"]: r["outcome"] for r in s["rows"]}
    assert outcomes == {"Dutch Fort": "correct", "Fish Market": "wrong_match", "Old Well": "false_abstain",
                        "Mystery Shop": "false_match", "Unknown Hut": "not_extracted", "Gone Tower": "not_extracted"}
    assert s["extraction_hits"] == 4 and s["extraction_recall"] == pytest.approx(4 / 6) and s["extraction_precision"] == pytest.approx(4 / 5)
    errors = {r["label"]: r["geo_error_m"] for r in s["rows"]}
    assert errors["Dutch Fort"] == 0.0 and 1500 < errors["Fish Market"] < 1700  # wrong pick: ~1.56 km from the truth
    total = score.aggregate([s])
    assert total["match_accuracy"] == pytest.approx(1 / 4) and total["false_match_on_unknown"] == 1.0 and total["geo_error_m"]["n"] == 2
    assert total["truth_missing_from_candidates"] == 1  # "node/9" is not in the candidate pool
