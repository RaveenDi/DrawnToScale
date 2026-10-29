# Proposed implementation structure

The tree below remains the target design. On 2026-10-29 the user requested implementation starting with Drawn to Scale. The first API/core now exists in [project/](../README.md), with offline tests and a pinned Python environment; the frontend, deployment and submission directories are not implemented. Public repository creation, commits and pushes follow the user's separate authorization rule in [AGENTS.md](../../AGENTS.md).

```text
drawn-to-scale/
  README.md
  AGENTS.md
  .env.example
  .gitignore
  pyproject.toml
  uv.lock
  apps/
    web/
      package.json
      package-lock.json
      src/
        app/                 # shell and routing
        features/sketch/      # upload and landmark editing
        features/map-review/  # candidate matching and confirmations
        features/export/
        components/          # reusable accessible controls
        api/                 # generated types and small API client
    api/
      app/
        main.py
        config.py
        api/                 # routes, authentication, request schemas
        domain/              # sketch graph, matching, alignment, export
        workflows/           # bounded step runner and states
        providers/           # Meta, geocoder, OSM adapters
        prompts/             # versioned templates and output schemas
        storage/             # runs, artifacts and stage events
        telemetry/           # timing, usage and evaluation IDs
      tests/
  evals/
    manifest.jsonl           # rights, split, area and reference provenance
    fixtures/                # small publishable examples
    ground_truth/            # evaluator only; never sent to model
    run.py
    reports/                 # measured results with input/version hashes
  docs/
    architecture.md
    data-and-attribution.md
    limitations.md
  deployment/
    Dockerfile
  submission/
    answers.md
    storyboard.md
```

Use one Python environment and one frontend package lock. Schemas and evaluation inputs are first-class code artifacts. Separate domain algorithms from model/provider calls so both can be tested independently. Do not build a vector database, microservice fleet, agent swarm or training pipeline for this scoped task.

Order: provider/schema smoke test → extraction evaluation → candidate matcher → geometry/correction → export → minimal UI → benchmark → polish/deploy/video. The full UI starts only after [the gate](04-FEASIBILITY-TEST.md) passes.

If all finalists are tested, budget up to 8 hours total for selection tests (3 mapping + 2 incremental combined + 3 AccessShift), then approximately 21 hours implementation, 6 hours integration/evaluation, 8 hours deployment/demo/docs and 2 hours buffer: **45 total**. Time already spent reduces remaining capacity. Stop testing alternatives when a clear choice is made. The internal deadline remains Wed Nov 4, 22:00 SLT / 11:30 AM ET, per calendar.
