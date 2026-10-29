# Test results — Drawn to Scale

Recorded 29 October 2026. **First API/core implemented and checked; full product feasibility gate NOT TESTED.**

## First implementation results

The user requested implementation starting with Drawn to Scale and selected Negombo. The local implementation is in [project/](../README.md), which remains ignored by this planning repository.

| Check | Actual result | Evidence boundary |
|---|---|---|
| Implementation test suite | **34 passed**, one dependency deprecation warning; exit 0 | Synthetic geometry and mocked-provider transport, upload → correction → export, access control, stale revisions, restart recovery, bounded retries, empty coverage and busy-worker behavior. No real model calls. |
| Local service startup | **Passed**, health and OpenAPI responses returned successfully | Service started on loopback, served nine documented route paths, and was shut down. Health correctly reported that the Meta key is not configured. |
| Negombo geographic retrieval | **259 named features** in the provisional central area | One real read-only Overpass retrieval. [Area config](../evals/areas/negombo-central.json); local candidate snapshot/manifest at `private/negombo-coverage.json`. Source: [OpenStreetMap](https://www.openstreetmap.org/node/2064194449), [attribution/licence](https://www.openstreetmap.org/copyright). Feature count is not proof of correct matches or complete coverage. |
| Real Meta extraction/matching | **NOT RUN** | No owned sketch or locally configured key has been supplied. |
| Full five-sketch gate | **NOT TESTED** | No extraction precision/recall, independently checked geographic error or manual-versus-assisted time has been measured. |

Reproduce the implementation tests from `project/` with `.venv\Scripts\python.exe -m pytest -q`. The Windows restricted runner blocked the test client's internal loopback connection; the same offline suite passed in the normal host runner. All external providers are mocked in these tests.

## Earlier preparation results

| Check | Actual result | What it establishes |
|---|---|---|
| Local core probe | **7/7 passed**; exit code 0 | Synthetic rotation/scale/translation fitting, outlier rejection, independent-point error, too-few/collinear/non-finite anchors and reflected geometry. |
| Local client/schema probe | **6/6 passed**; exit code 0 | Prepared synthetic output accepted; unexpected/missing fields, invalid types/enums and bad semantic references or geometry rejected; default execution reads no key and makes no network request |
| Configuration dry run | Passed | Prompt/schema files load; no input image supplied; zero model calls |
| Real Meta inference | **NOT RUN** | User instructed “Prepare the tests first”; no live call was made |
| Complete product gate | **NOT TESTED** | Requires the real inputs and protocol in [04-FEASIBILITY-TEST.md](04-FEASIBILITY-TEST.md) |

## Scope of the evidence

In the earlier probes, correspondences were supplied; they did not test real sketch extraction, geographic retrieval, a corrected-map workflow, user timing or a live model. The newer implementation checks and real geographic retrieval are recorded separately above. Real sketch extraction and user timing remain untested.

The hand-authored `fixture.synthetic.json` is validator input, not a captured model response. The local checks are small engineering probes, not benchmark results or evidence of adoption, time saved, field safety or winning odds.

Reproduce with `python spike/check_core.py` and `python spike/check_probe.py` from this project folder. Saved machine-readable output: [results/local-checks.json](../spike/results/local-checks.json). These scripts and reports may be published only under the eventual project/data rules; they do not constitute a submitted product.

## Next evidence needed

Collect five owned sketches of known areas and independent reference matches. Start with one real image extraction call, then run the three-hour mapping protocol.

Record every live attempt, failure and correction. Store model outputs under the local gitignored `spike/private/` directory until reviewed for sharing. Update this record after the real test; do not relabel a synthetic pass as a model pass.
