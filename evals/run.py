"""Evaluation harness: run the real pipeline on a manifest, then score against ground truth.

    python evals/run.py --provider mock --manifest evals/manifest.demo.jsonl --split dev
    python evals/run.py --provider replay --manifest evals/manifest.jsonl --split dev
    python evals/run.py --provider meta --manifest evals/manifest.jsonl --split dev --max-calls 60   # live, billed

Ground truth is read only by score.py after a run finishes. The pipeline receives the image, the area box
and an empty context, nothing else. See evals/README.md for the dev/held-out rule."""
import argparse
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
from io import StringIO
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
for extra in (ROOT / "apps" / "api", ROOT):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

from app.config import PRICES_USD_PER_MTOK, Settings  # noqa: E402
from app.contracts import Area  # noqa: E402
from app.llm import ProviderError, Transport  # noqa: E402
from app.mock import FixtureGeography, MockProvider  # noqa: E402
from app.providers import MetaProvider, OSMProvider, prepare_image  # noqa: E402
from app.storage import Store  # noqa: E402
from app.workflow import Workflow  # noqa: E402
from evals import score  # noqa: E402

REPORTS = ROOT / "evals" / "reports"
REQUIRED = ("id", "image", "area", "split", "rights", "ground_truth")


class ManifestError(ValueError):
    pass


def load_manifest(path):
    path = Path(path)
    items, seen = [], set()
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        try:
            item = json.loads(line)
        except ValueError:
            raise ManifestError(f"{path.name} line {number}: not valid JSON") from None
        missing = [k for k in REQUIRED if k not in item]
        if missing:
            raise ManifestError(f"{path.name} line {number}: missing {', '.join(missing)}")
        if item["split"] not in {"dev", "heldout"}:
            raise ManifestError(f"{item['id']}: split must be dev or heldout")
        if not str(item["rights"]).strip() or not isinstance(item["ground_truth"], dict) or not item["ground_truth"]:
            raise ManifestError(f"{item['id']}: rights and a non-empty ground_truth {{sketch_label: osm_id|null}} are required")
        if any(v is not None and not isinstance(v, str) for v in item["ground_truth"].values()):
            raise ManifestError(f"{item['id']}: ground_truth values must be an OSM id string or null")
        if item["id"] in seen:
            raise ManifestError(f"duplicate id {item['id']}")
        seen.add(item["id"])
        image = Path(item["image"])
        item["_image_path"] = image if image.is_absolute() else next((b / image for b in (ROOT, path.parent) if (b / image).is_file()), ROOT / image)
        if not item["_image_path"].is_file():
            raise ManifestError(f"{item['id']}: image not found: {item['image']}")
        items.append(item)
    if not items:
        raise ManifestError(f"{path.name} has no items")
    return items


def resolve_area(spec):
    if isinstance(spec, dict):
        return Area.model_validate(spec)
    return Area.model_validate(json.loads((ROOT / "evals" / "areas" / f"{spec}.json").read_text(encoding="utf-8"))["area"])


def request_settings():
    """Load only non-secret request settings; never parse keys or interpolate secrets."""
    from dotenv import dotenv_values
    names = {"META_MODEL", "META_REASONING_EFFORT"}
    lines = []
    path = ROOT / ".env"
    if path.is_file():
        with path.open(encoding="utf-8") as source:
            for line in source:
                name = line.split("=", 1)[0].strip().removeprefix("export ").strip()
                if name in names:
                    lines.append(line)
    values = dotenv_values(stream=StringIO("".join(lines)), interpolate=False)
    return {name: os.environ.get(name, values.get(name) or "") for name in names}


def build_providers(kind, recordings, settings):
    if kind == "mock":
        return MockProvider(), FixtureGeography()
    cache = ROOT / "private" / "geography-cache"
    config = request_settings()
    model = config["META_MODEL"].strip() or "muse-spark-1.3"
    effort = config["META_REASONING_EFFORT"].strip() or None
    if kind == "replay":
        return (MetaProvider("", model, transport=Transport(mode="replay", recordings=recordings), reasoning_effort=effort), OSMProvider(cache, offline=True))
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env", override=False)
    key = os.getenv("MODEL_API_KEY", "").strip()
    if not key:
        raise SystemExit("--provider meta needs MODEL_API_KEY in .env (live, billed calls).")
    transport = Transport(key, recordings=recordings, retries=settings.http_retries, backoff_s=settings.backoff_s,
                          max_wait_s=settings.max_retry_wait_s)
    return MetaProvider(key, model, transport=transport, reasoning_effort=effort), OSMProvider(cache)


def run_item(workflow, store, item, area):
    """Feed the pipeline exactly what a user would: image + area + empty context. No ground truth."""
    if isinstance(workflow.meta, MockProvider):
        workflow.meta.layout = item.get("mock_layout", "demo-sketch")  # mock only: picks a canned fixture layout
    normalized, info = prepare_image(item["_image_path"].read_bytes())
    run, _ = store.create(area, normalized, info, "")
    error = None
    try:
        workflow.process(run["id"])
    except ProviderError as exc:
        error = str(exc)
    except Exception as exc:  # reported per item; the class name is safe, the message may not be
        error = f"Unexpected pipeline error ({type(exc).__name__})"
    if error:
        workflow.stage(run["id"], "failed", error=error)
    return store.get(run["id"])


def item_result(item, run):
    candidates = {c["id"]: (c["longitude"], c["latitude"]) for c in run["candidates"]}
    scored = score.score_item(item["ground_truth"], run["graph"], run["proposals"], candidates)
    verdict = run.get("verification") or (run.get("agent") or {}).get("verification") or {}
    verified_proposals = run["proposals"] if verdict.get("status") == "pass" else [
        {**p, "candidate_id": None} for p in run["proposals"]]
    verified = score.score_item(item["ground_truth"], run["graph"], verified_proposals, candidates)
    totals = run.get("totals") or {}
    verifier = [s for s in run.get("trace", []) if s["agent"] == "Verifier"]
    return {"id": item["id"], "split": item["split"], "area": item["area"] if isinstance(item["area"], str) else "inline",
            "rights": item["rights"], "run_status": run["status"], "error": run.get("error"),
            "agent": run.get("agent"), "verifier_final": verifier[-1]["status"] if verifier else None,
            "steps": totals.get("model_turns"), "tool_calls": totals.get("tool_calls"), "model_calls": run["model_calls"],
            "input_tokens": totals.get("input_tokens"), "output_tokens": totals.get("output_tokens"),
            "model_latency_ms": totals.get("model_latency_ms"), "wall_ms": totals.get("wall_ms"),
            "est_cost_usd": totals.get("est_cost_usd"), "cost_basis": totals.get("cost_basis"), "score": scored,
            "raw_score": scored, "verified_score": verified,
            "raw_proposal_accuracy": score.ratio(scored["correct"], scored["gt_with_id"]),
            "verified_accuracy": score.ratio(verified["correct"], verified["gt_with_id"]),
            "verification": verdict}


def telemetry_summary(results):
    def total(key):
        values = [r[key] for r in results if r[key] is not None]
        return sum(values) if values else None
    n = len(results) or 1
    return {"model_calls": total("model_calls"), "steps": total("steps"), "tool_calls": total("tool_calls"),
            "input_tokens": total("input_tokens"), "output_tokens": total("output_tokens"),
            "tokens_reported": any(r["input_tokens"] is not None for r in results),
            "model_latency_ms": total("model_latency_ms"), "wall_ms": total("wall_ms"),
            "mean_wall_ms_per_item": round((total("wall_ms") or 0) / n), "est_cost_usd": total("est_cost_usd"),
            "cost_basis": next((r["cost_basis"] for r in results if r["cost_basis"]), None)}


EVIDENCE = {"mock": "NONE. Offline mock provider and a synthetic fixture: tests plumbing only, says nothing about Meta models.",
            "replay": "Recorded live model responses replayed offline (see private/recordings). Quality claims need the matching live run.",
            "meta": "Live Meta Model API calls."}


def previous_heldout(out_dir, manifest_sha):
    """Name of an earlier non-mock held-out report for this exact manifest, if any."""
    for path in sorted(Path(out_dir).glob("*-heldout.json")):
        try:
            meta = json.loads(path.read_text(encoding="utf-8"))["meta"]
        except (ValueError, KeyError):
            continue
        if meta.get("manifest_sha256") == manifest_sha and meta.get("provider") != "mock":
            return path.name
    return None


def evaluate(provider, manifest_path, split, *, max_calls=None, out_dir=REPORTS, meta=None, geography=None,
             recordings=None, data_dir=None, settings=None, allow_repeat_heldout=False):
    manifest_path = Path(manifest_path)
    items = [i for i in load_manifest(manifest_path) if i["split"] == split]
    if not items:
        raise ManifestError(f"no '{split}' items in {manifest_path.name}")
    manifest_sha = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
    out_dir = Path(out_dir)
    if split == "heldout" and provider != "mock" and not allow_repeat_heldout:
        earlier = previous_heldout(out_dir, manifest_sha)
        if earlier:
            raise SystemExit(f"Held-out split already reported ({earlier}). Report it once; pass --allow-repeat-heldout to override.")
    if provider == "meta" and not max_calls:
        raise SystemExit("--provider meta is billed: pass --max-calls N.")
    settings = settings or Settings.from_env()
    recordings = Path(recordings or ROOT / "private" / "recordings")
    if meta is None:
        meta, geography = build_providers(provider, recordings, settings)
    data_dir = Path(data_dir or tempfile.mkdtemp(prefix="eval-", dir=_private()))
    store = Store(data_dir)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    results, used, skipped = [], 0, []
    workflow = Workflow(store, meta, geography, settings)
    try:
        for item in items:
            remaining = None if max_calls is None else max_calls - used
            if remaining is not None and remaining < 2:
                skipped.append(item["id"])
                continue
            if remaining is not None:
                workflow.settings = replace(settings, max_model_calls=min(settings.max_model_calls, remaining))
            run = run_item(workflow, store, item, resolve_area(item["area"]))
            used += run["model_calls"]
            results.append(item_result(item, run))
    finally:
        workflow.close()
        client = getattr(meta, "client", None)
        if client:
            client.close()
    scores = [r["score"] for r in results]
    raw_metrics = score.aggregate(scores)
    verified_metrics = score.aggregate([r["verified_score"] for r in results])
    report = {"meta": {"stamp": stamp, "provider": provider, "split": split, "manifest": manifest_path.name, "manifest_sha256": manifest_sha,
                       "git_commit": _git_commit(), "model": getattr(meta, "model", None), "max_calls": max_calls, "calls_used": used,
                       "items_run": len(results), "items_skipped_for_call_cap": skipped, "evidence": EVIDENCE[provider],
                       "caps": {k: v for k, v in settings.__dict__.items()},
                       "price_table": {m: {"verified": p["verified"], "source": p["source"]} for m, p in PRICES_USD_PER_MTOK.items()},
                       "held_out": split == "heldout"},
              "aggregate": {**raw_metrics,
                          "raw_proposal_accuracy": raw_metrics["match_accuracy"],
                          "verified_accuracy": verified_metrics["match_accuracy"],
                          "raw": raw_metrics,
                          "verified": verified_metrics,
                          "telemetry": telemetry_summary(results)} if scores else None,
              "items": results}
    out_dir.mkdir(parents=True, exist_ok=True)
    base = out_dir / f"{stamp}-{split}"
    base.with_suffix(".json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    base.with_suffix(".md").write_text(markdown(report), encoding="utf-8")
    return report, base


def _private():
    path = ROOT / "private"
    path.mkdir(exist_ok=True)
    return path


def _git_commit():
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True, timeout=5).stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


def pct(x):
    return "n/a" if x is None else f"{x * 100:.0f}%"


def markdown(report):
    m, a = report["meta"], report["aggregate"]
    lines = [f"# Evaluation report: {m['split']} split, provider `{m['provider']}`", "",
             f"- Evidence: **{m['evidence']}**",
             f"- Manifest `{m['manifest']}` (sha256 `{m['manifest_sha256'][:12]}`), commit `{m['git_commit']}`, {m['stamp']}",
             f"- Items run: {m['items_run']}; model calls used: {m['calls_used']}" + (f" of cap {m['max_calls']}" if m["max_calls"] else "")]
    if m["held_out"]:
        lines.append("- **Held-out split.** Report this once; do not tune on it.")
    if m["items_skipped_for_call_cap"]:
        lines.append(f"- Skipped (call cap): {', '.join(m['items_skipped_for_call_cap'])}")
    if not a:
        return "\n".join(lines + ["", "No items were run."]) + "\n"
    t, g = a["telemetry"], a["geo_error_m"]
    lines += ["", "## Aggregate (micro-average over landmarks)", "", "| Metric | Value |", "|---|---|",
              f"| Extraction recall / precision | {pct(a['extraction_recall'])} / {pct(a['extraction_precision'])} ({a['ground_truth_landmarks']} labelled, {a['extracted_landmarks']} extracted) |",
              f"| Raw / verified accuracy | {pct(a['raw_proposal_accuracy'])} / {pct(a['verified_accuracy'])} |",
              f"| Match accuracy (labelled with an OSM id) | {pct(a['match_accuracy'])}; wrong {pct(a['wrong_match_rate'])}, false abstain {pct(a['false_abstain_rate'])} |",
              f"| Abstain correctness (labelled unknown) | {pct(a['abstain_correctness'])}; false match {pct(a['false_match_on_unknown'])} |",
              f"| Geographic error vs ground truth ({'held-out' if m['held_out'] else m['split']}) | " + (f"median {g['median']} m, mean {g['mean']} m, p90 {g['p90']} m, max {g['max']} m (n={g['n']})" if g else "n/a") + " |",
              f"| Ground truth missing from OSM candidates | {a['truth_missing_from_candidates']} (coverage/shortlist miss, not a model error) |",
              f"| Steps / tool calls / model calls | {t['steps']} / {t['tool_calls']} / {t['model_calls']} |",
              f"| Tokens in / out | " + (f"{t['input_tokens']} / {t['output_tokens']}" if t["tokens_reported"] else "not reported") + " |",
              f"| Model latency / wall time | {t['model_latency_ms']} ms / {t['wall_ms']} ms (mean {t['mean_wall_ms_per_item']} ms per item) |",
              f"| Estimated cost | " + (f"${t['est_cost_usd']:.4f} ({t['cost_basis']})" if t["est_cost_usd"] is not None else "n/a") + " |",
              "", "## Items", "", "| Item | Status | Agent | Recall | Precision | Correct | Wrong | False abstain | Correct abstain | False match | Calls |", "|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in report["items"]:
        s = r["score"]
        lines.append(f"| {r['id']} | {r['run_status']}{' (' + r['error'] + ')' if r['error'] else ''} | {(r['agent'] or {}).get('status', 'n/a')} | {pct(s['extraction_recall'])} | {pct(s['extraction_precision'])} | "
                     f"{s['correct']}/{s['gt_with_id']} | {s['wrong_match']} | {s['false_abstain']} | {s['correct_abstain']}/{s['gt_null_extracted']} | {s['false_match']} | {r['model_calls']} |")
    lines += ["", "Verified means the entire anchor set passed geometry; it does not mean human-confirmed or ground-truth correct.",
              "", "| Item | Raw proposal accuracy | Verified accuracy |", "|---|---|---|"]
    for r in report["items"]:
        lines.append(f"| {r['id']} | {pct(r['raw_proposal_accuracy'])} | {pct(r['verified_accuracy'])} |")
    lines += ["", "## Per-landmark outcomes", "", "| Item | Label | Truth | Proposed | Outcome | Error (m) |", "|---|---|---|---|---|---|"]
    for r in report["items"]:
        for row in r["score"]["rows"]:
            lines.append(f"| {r['id']} | {row['label']} | {row['truth']} | {row['proposed']} | {row['outcome']} | {row['geo_error_m']} |")
    return "\n".join(lines) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--provider", choices=["mock", "replay", "meta"], required=True)
    ap.add_argument("--manifest", default="evals/manifest.jsonl")
    ap.add_argument("--split", choices=["dev", "heldout"], required=True)
    ap.add_argument("--max-calls", type=int, help="total model-call budget for the whole evaluation (required for meta)")
    ap.add_argument("--out", default=str(REPORTS))
    ap.add_argument("--allow-repeat-heldout", action="store_true")
    args = ap.parse_args(argv)
    manifest = Path(args.manifest)
    manifest = manifest if manifest.is_absolute() else ROOT / manifest
    try:
        report, base = evaluate(args.provider, manifest, args.split, max_calls=args.max_calls, out_dir=args.out,
                                allow_repeat_heldout=args.allow_repeat_heldout)
    except (ManifestError, ProviderError) as exc:
        raise SystemExit(f"error: {exc}") from None
    print(markdown(report).split("## Items")[0])
    print(f"Wrote {base.with_suffix('.json')} and {base.with_suffix('.md')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
