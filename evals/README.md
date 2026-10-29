# First real-sketch gate

User-selected location: Negombo. The area JSON is a provisional first test boundary and may be changed to fit the user's drawing before locking evaluation cases.

No real sketch inputs or evaluator answers are included. The candidate snapshot under `private/` is geographic input only; it is not ground truth. Synthetic tests under `apps/api/tests/` cannot pass this gate.

Prepare five owned sketches with at least four identifiable landmarks. Keep two unseen during prompt tuning. Include rotation, uneven scale, duplicate place names, an unmapped feature and a contradictory relation across the set.

Keep reference locations and expected identities separate from model-visible context. Record rights, author, area, split, image hash and independently checked source IDs before testing. Never include answers in image filenames or context.

Record extraction precision/recall, proposed/corrected matches, correction count and manual-versus-assisted time. Report fit residuals separately from error against unused reference points. A proposed match is not a correct match merely because the model selected an existing ID.

The planning gate requires at least 80% named-landmark precision and recall; at least three of five sketches (including one held out) with three correct matches within 100 m and no more than two corrections each; abstention on ambiguity; and honestly measured completion time. The 100 m threshold is a spike threshold, not navigation or survey accuracy.

Current status: **NOT TESTED**. Real model input and local credentials are pending.
