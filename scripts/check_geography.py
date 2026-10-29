"""Read-only public geographic coverage check; no model or evaluator ground truth."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps/api"))

from app.contracts import Area
from app.providers import OSMProvider, ProviderError


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--area", type=Path, default=ROOT / "evals/areas/negombo-central.json")
    args = parser.parse_args()
    area = Area.model_validate(json.loads(args.area.read_text(encoding="utf-8"))["area"])
    provider = OSMProvider(ROOT / "private/geography-cache")
    try:
        candidates, manifest = provider.retrieve(area)
    except ProviderError as error:
        print(json.dumps({"status": "failed", "error": str(error)}))
        return 1
    finally:
        provider.client.close()
    report = {"area": area.model_dump(), "manifest": manifest, "candidates": [c.model_dump() for c in candidates],
              "model_calls": 0, "sketch_tested": False, "note": "Retrieval only; candidate correctness and coverage still need manual inspection."}
    output = ROOT / "private/negombo-coverage.json"
    output.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({"status": "retrieved", "candidate_count": len(candidates),
                      "report": str(output), "model_calls": 0, "manifest": manifest}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
