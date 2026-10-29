# Core workflow

## One complete task

1. **Locate the area.** User searches a town or pins a bounding box. The app confirms coverage before processing; it does not guess the location of an arbitrary drawing anywhere on Earth.
2. **Upload a sketch.** Accept one JPEG/PNG. Correct image orientation and show the actual image sent for extraction. Ask about a north arrow only if present; page-up is not automatically north.
3. **Review interpretation.** Extract landmark names/types, normalized image positions, and explicitly drawn relationships. Show editable numbered annotations on the original. Unknown text stays unknown.
4. **Propose matches.** Retrieve geographic candidates inside the chosen area. Display source names and identifiers; the model may select candidate IDs but cannot fabricate coordinates.
5. **Resolve ambiguity.** A split view shows sketch and map. Ask a targeted question such as which of two schools is intended, or ask the user to pin an anchor. Preserve the original interpretation and all corrections.
6. **Check geometry.** Fit a bounded transform to accepted anchors; report residuals and mismatches. A low fit residual on the fitting anchors does not prove location correctness. Use unused reference points for evaluation.
7. **Export.** Export confirmed geographic points and explicit user-described features as GeoJSON, with source, status and provenance. Unmatched sketch items stay in the report; inferred paths remain marked approximate. Include a printable review summary.

## First interface after the spike

One workspace: upload/area controls, original sketch, geographic map, a short review queue, and export. Show processing stage and retry actions. Use status labels such as **proposed**, **confirmed**, **needs review** and **unmatched**; avoid percentages pretending to be calibrated confidence. Keep edits usable with keyboard controls.

## MVP and extensions

MVP: known area, 4–10 landmarks, one drawing, candidate matching, explicit corrections, evidence and export. Support English labels first; other scripts are capability tests, not assumed support. Prioritize independent accuracy and effort measurement.

Optional after the gate: a short transcript clarifying landmark labels, followed by real recorded speech if it demonstrably helps. Optional globe transition is presentation work. Settlement omission audits, live pen tracking, crowdsourced consensus, legal boundaries and arbitrary-region discovery are outside the first deliverable.

## Impact evidence and demo

Measure manual placement time versus assisted placement, total corrections, matched/unmatched landmarks, wrong matches and error in metres on points not used to fit the transform. A builder-only test supports a workflow claim, not community adoption.

Proposed three-minute story: show a real drawing; interpret it; correct one ambiguity; show matched landmarks and an honestly unmatched item; export and open the file; display the evaluation and limits. Use test results only after they exist.
