# Drawn to Scale

Status: **first local API/core implementation built; real-model spike pending**. Updated 2026-10-29. The user requested implementation starting here and selected **Negombo** for the first real test. This folder retains planning and probes; the runnable implementation is in [../](../README.md), ignored by the planning repository and not yet a separate repository or completed app.

The first core connects extraction, bounded OSM retrieval, candidate proposals, user correction, geometry checks and confirmed-point export. Offline integration checks use synthetic inputs and mocked model responses. A real central Negombo query returned 259 named geographic features; that is retrieval evidence only. Full UI work and the product gate remain pending real owned sketches and a local key.

**Promise:** turn a freehand map of a known area into geographic features that the author can check, correct and export. The first beneficiary is a person digitizing their own local map. Wider community impact remains a hypothesis.

| Read | Purpose |
|---|---|
| [01-WORKFLOW.md](01-WORKFLOW.md) | What the user does, what the app returns and the scope boundary |
| [02-ARCHITECTURE.md](02-ARCHITECTURE.md) | Components, model roles, data contracts and failure handling |
| [03-PROJECT-STRUCTURE.md](03-PROJECT-STRUCTURE.md) | Proposed implementation tree and development order |
| [04-FEASIBILITY-TEST.md](04-FEASIBILITY-TEST.md) | Three-hour experiment and decision gate |
| [05-TEST-RESULTS.md](05-TEST-RESULTS.md) | Executed checks, pending evidence and limitations |

Run the local math checks from the planning-repo root: `python drawn-to-scale/spike/check_core.py`. They do not call a model or establish sketch-reading performance.

Budget: **45 hours maximum across selection, testing and ONE delivered entry**, not 45 hours for each finalist. A three-hour core spike comes first. The [combined finalist](../../map-what-people-know/docs/README.md) can reuse the mapping experiment and test the incremental value of speech. [AccessShift](../../access-shift/docs/README.md) is a separate product alternative.

The project scores in [the final ranking](../../FINAL-IDEA-RANKING.md) are subjective estimates. The competitive contribution to prove is reliable interpretation plus correction and export, with measured effort/error. No winning probability is established.

Sources: [original concept](../../anthropic/ideas/idea-15-drawn-to-scale.md), [adversarial review](../../anthropic/research/round3-redteam-drawn-to-scale.md), [scorecard](../../12-IDEA-SCORECARD.md), [HeiGIT's existing paper-map workflow](https://sketch-map-tool.heigit.org/about). The existence of prior systems rules out a world-first claim.
