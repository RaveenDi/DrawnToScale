# First feasibility experiment — three-hour ceiling

## Hardest assumption

A model can read enough named landmarks and relationships from a distorted memory sketch to propose correct geographic matches inside a known area, with a manageable number of corrections. Perfect geometric fitting to supplied points would not test this assumption.

## Inputs and preparation

- Five independently made sketches of known areas, at least four identifiable landmarks each. Self-created sketches are acceptable for the spike; report that they are one-person data. Keep at least two unseen during prompt tuning.
- Include rotation, uneven scale, an ambiguous duplicate name, an unmapped feature and a contradictory relation across the cases. Use owned inputs and check the candidate-data attribution/licence.
- Prepare reference geographic IDs/coordinates by manual inspection, separately from model-visible files. Candidate retrieval is allowed to include the correct place among alternatives; it must not label it as correct.
- Before model calls, lock the test cases, expected correspondences and proposed thresholds. Do not rename images with answers or expose reference coordinates in the extraction prompt.

## Execution

| Elapsed | Work |
|---|---|
| 0–30 min | Confirm real image call and schema output; record model/prompt version; manually verify area coverage |
| 30–75 min | Extract sketch graph; measure landmark precision/recall and coordinate validity |
| 75–135 min | Retrieve bounded candidates, propose matches, fit checked anchors, ask for one correction where needed |
| 135–180 min | Run untouched cases, compare manual workflow, collect all failures and make the go/no-go decision |

## Proposed pass gate (not achieved)

1. Named-landmark extraction precision and recall each at least 80% over the five sketches; report counts and denominators.
2. At least 3/5 sketches, including at least one untouched sketch, have three correct geographic matches within 100 m of independently recorded reference points with no more than two user corrections each. This coarse location threshold is a spike criterion, not survey accuracy or navigation suitability.
3. Ambiguous/unknown features remain unresolved until evidence or user confirmation exists; include at least one challenge case where the app must abstain.
4. Report manual versus assisted completion time, including correction effort. If assistance is slower, do not claim time savings.
5. Report fitting-anchor residuals separately from independent reference error. Never evaluate a transform solely on the points used to fit it.

Failing data coverage means a coverage limitation, not model success/failure. If extraction passes but geography fails, narrow the supported area and retest once within budget. If the core remains unreliable, stop UI work and compare AccessShift.

## Prepared tools

`python drawn-to-scale/spike/check_core.py` checks fitting/outlier/degeneracy logic on explicitly synthetic known correspondences. [05-TEST-RESULTS.md](05-TEST-RESULTS.md) records its actual outcome. It does not evaluate the model or geographic retrieval.

The prepared `spike/model_probe.py` can run image-to-JSON extraction later with `--image`, `--text`, `--schema`, `--prompt` and `--output` arguments. It reads a locally configured key; do not paste it into chat. The extraction schema/prompt live in `spike/`. Real calls are intentionally pending after the user's instruction to prepare tests first. Full geographic matching remains to be implemented after extraction evidence; the probe is not an end-to-end app.
