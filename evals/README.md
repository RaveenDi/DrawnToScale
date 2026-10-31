# Evaluations

`evals/run.py` runs the real pipeline (extract, retrieve, tool-calling match, Verifier) on a manifest of owned sketches and scores it against ground truth **you wrote beforehand**. Ground truth is read only by `evals/score.py` after a run finishes; the pipeline gets the image, the area box and an empty context (a test inspects every outgoing request).

```powershell
.venv\Scripts\python.exe evals\run.py --provider mock   --manifest evals\manifest.demo.jsonl --split dev       # offline, plumbing only
.venv\Scripts\python.exe evals\run.py --provider replay --manifest evals\manifest.jsonl --split dev             # recorded live answers, free
.venv\Scripts\python.exe evals\run.py --provider meta   --manifest evals\manifest.jsonl --split dev --max-calls 60   # live, billed
```

`--max-calls N` is a budget for the whole evaluation (required for `meta`); an item that cannot start within it is skipped and listed. Reports go to `evals/reports/<stamp>-<split>.json` and `.md` (gitignored; `git add -f` the final held-out report). `mock` results are labelled **NONE** as evidence: the demo manifest only proves the harness works. In the demo, the held-out item is a mirror-image sketch: names match but no rotation+scale+shift fits, so the pipeline abstains (reported as false abstains, never as wrong coordinates).

## Manifest line (`evals/manifest.jsonl`, one JSON object per line)

```json
{"id": "s01", "image": "private/sketches/s01.jpg", "area": "negombo-central", "split": "dev", "rights": "own drawing, 2026-10-30",
 "ground_truth": {"Dutch Fort": "osm:way/196057979", "Cousin's shop": null}}
```

- `area`: a file name in `evals/areas/` or an inline `{west,south,east,north}` box. `split`: `dev` or `heldout`. `rights`: who owns the image (required).
- `ground_truth`: **every landmark drawn on the sketch**, keyed by the label as written on the sketch, valued by the OSM id (`way/123`, `node/123` or `osm:...`) or `null` when the place is not in OSM or you cannot identify it. Keys are matched to extracted labels by name similarity (>= 0.8).
- Optional `mock_layout` (mock provider only) picks a canned fixture layout. Never put answers in file names or context.

## Write ground truth BEFORE running

1. Draw/collect the sketch, then look each place up on openstreetmap.org yourself and write the id into the manifest. Commit or at least save the manifest **before** the first model run.
2. Check the id exists in the area's OSM snapshot; otherwise the report counts "ground truth missing from OSM candidates" (a coverage problem, not a model error).
3. Do not edit ground truth after seeing model output. If you made a mistake, say so in the notes and re-run.

## Dev vs held-out

- Tune prompts, caps and tool descriptions on `dev` only. Hold back at least two sketches as `heldout` and **do not run them while tuning**.
- Run `heldout` once, at the end. The harness refuses a second non-mock held-out report for the same manifest unless `--allow-repeat-heldout` (then say so when you quote it).
- Never report dev numbers as held-out results, or recordings as a fresh live run.

## Trap cases to include

Wrong label (misspelled or a local name that OSM spells differently), unknown place (drawn but not in OSM: truth `null`), ambiguous name (two plausible OSM features), reflected sketch (mirror image), plus rotation and uneven scale. Expected good behaviour: abstain on unknown and ambiguous; the Verifier rejects the reflected one.

## Metrics

Extraction recall/precision (ground-truth labels vs extracted landmarks); match accuracy over labels with an OSM id (and wrong-match / false-abstain rates); abstain correctness over labels with `null` (false match rate); geographic error in metres between the proposed place and the true place (0 when right; abstentions have no position), reported as median/mean/p90/max, and labelled held-out for that split; steps, tool calls, model calls, tokens (only if the API returned them), model latency, wall time and estimated cost. Cost uses `config.py` placeholder prices marked **UNVERIFIED**; replace them with published rates before quoting dollars.

## First real-sketch gate (original protocol)

User-selected location: Negombo. The area JSON is a provisional first test boundary and may be changed to fit the user's drawing before locking evaluation cases. No real sketch inputs or evaluator answers are included. The candidate snapshot under `private/` is geographic input only, not ground truth. Synthetic tests under `apps/api/tests/` cannot pass this gate.

Prepare five owned sketches with at least four identifiable landmarks. Keep two unseen during prompt tuning (these are the `heldout` items). Include rotation, uneven scale, duplicate place names, an unmapped feature and a contradictory relation across the set. Record rights, author, area, split, image hash and independently checked source IDs before testing.

Record extraction precision/recall, proposed/corrected matches, correction count and manual-versus-assisted time. Report fit residuals separately from error against unused reference points. A proposed match is not a correct match merely because the model selected an existing ID.

The planning gate requires at least 80% named-landmark precision and recall; at least three of five sketches (including one held out) with three correct matches within 100 m and no more than two corrections each; abstention on ambiguity; and honestly measured completion time. The 100 m threshold is a spike threshold, not navigation or survey accuracy.

Current status: **NOT TESTED**. Real model input and local credentials are pending.


### Raw versus verified proposals

New JSON and Markdown reports include `raw_proposal_accuracy` and
`verified_accuracy` per sketch and in the micro-aggregate. Both use all labelled
landmarks with an OSM ID as the denominator. Raw scoring uses the model's final
suggestions, which verification no longer deletes. Verified scoring admits
suggestions only when the entire anchor set has a `pass` verdict; review-required,
rejected, or absent verdicts contribute abstentions. Verified does not mean
human-confirmed or ground-truth correct. JSON includes full `raw_score` and
`verified_score` outcomes per item and `raw`/`verified` aggregate metrics;
legacy `score` and aggregate `match_accuracy` now describe raw proposals.
Historical reports are unchanged and cannot recover erased proposals without
separate recording analysis. No new live accuracy or cost reduction has been
measured. Shortlists are appended to model context on the first turn and only
when changed; the API still resends conversation history on subsequent calls.
