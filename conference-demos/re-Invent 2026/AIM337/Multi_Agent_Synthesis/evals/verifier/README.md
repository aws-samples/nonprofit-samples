# Evaluate the Verifier

Copyright (c) 2026 Amazon Web Services
SPDX-License-Identifier: MIT

This suite checks whether the Verifier assigns the right claim status and explains
it using the supplied evidence. It calls the same agent and runtime as the application,
with fixed inputs instead of running the full pipeline.

The application uses **Verifier handoff candidate v1 with Sonnet 5**, with a
15,000-token response limit.
See the [selected prompt](prompts/verifier-handoff-candidate-v1.txt) and
[results and limitations](RESULTS.md). The dataset has **12 reviewed cases and 36
claims**. All cases are development data; this is not a broad reliability benchmark.

## Setup and offline checks

From the repository root, install the pinned AWS and development dependencies:

```bash
uv sync --locked --extra aws --extra dev
source .venv/bin/activate
python evals/verifier/validate_dataset.py
pytest evals/verifier
```

These checks make no model calls. They validate source hashes, evidence excerpts,
input schemas, reference coverage, arithmetic, and the evaluation code. Tests use
fake responses and do not measure model quality.

## Run the Verifier evaluation

Configure credentials for direct Bedrock model invocation. An AgentCore deployment
is not needed for these evaluations.

```bash
export AWS_PROFILE=your-profile
python evals/verifier/run_baseline.py --region us-east-1 --case V001 --trials 1
```

The default is a preview: it prints the cases, prompt hash, model, limits, and
whether the pinned runtime is installed. It does not contact a model.

Add `--execute` to make paid calls:

```bash
python evals/verifier/run_baseline.py --region us-east-1 --case V001 --trials 1 --execute
```

For all 12 cases, omit `--case` and use `--trials 3`. This makes 36 Verifier
invocations and reviews 108 claims. Options include `--model-id`, `--prompt-file`,
`--diagnostics`, `--timeout-seconds`, and `--token-budget`. Overrides apply only to
that evaluation instance. Without an override, the runner uses the application's
prompt and settings, including environment overrides.

The runner prints a new directory under `runs/verifier-baseline-*`. It contains:

- The selected cases, prompt, configuration, and source hashes.
- Each trial's output, verdict comparisons, latency, and token usage.
- `summary.json` and `REVIEW_RESULTS.md` for inspecting the actual explanations.

Failed calls stop the batch; unattempted trials are reported separately. Invalid
outputs count as verdict nonmatches. Token budgets are checked between calls, so
the final call can exceed the remaining budget. Token totals are not dollar costs.
Generated runs stay local through `.gitignore`.

## Evaluate the explanations

A correct status can still have an unsupported or incomplete explanation. The
optional [explanation judge](explanation_judge/README.md) grades each saved reason
for grounding, completeness, and support for the returned status. It uses a separate
Opus 5.5 model call and does not rerun the Verifier.

Inspect judge failures and a sample of passes. Judge grades are diagnostic signals,
not substitutes for human review. The included calibration examples help check the
judge itself before using it to compare application changes.

## Dataset and reference judgments

[dataset.json](dataset.json) contains the complete `VerificationInput` for each case
and its separate reference verdicts, rationales, and required explanation meanings.
Only `case["input"]` reaches the Verifier. Reference answers, reviewer notes, and case
names stay outside its prompt.

The reference statuses mean:

- **Verified:** Evidence supports the claim as stated, including its material scope.
  Correct arithmetic and bounded inferences can be supported by one source.
- **Qualified:** The core is supported, but the review must attach a material omitted
  condition, limitation, or uncertainty.
- **Rejected:** The substantive assertion is unsupported or contradicted. A caveat
  cannot repair a wrong number, measure, population, or period.

Reference v1.2 contains 15 Verified, 17 Rejected, and four Qualified judgments. The
IPCC confidence label was revised after model outputs were reviewed; the dataset
records that it was not frozen before evaluation. This history matters when
interpreting the saved results. Claim confidence is fixed at 0.5 as a fixture value,
not a calibrated probability or a target for the Verifier to change.

Cases cover time periods, units, arithmetic, geographic scope, causal inference,
uncertainty, and conditional or preliminary estimates. Contradiction quality,
revision feedback, citation closure, and independent-source counts are not separately
scored. These are stage evaluations, not tests of the full pipeline or its safety gate.

## Sources and extending the suite

[source-manifest.json](source-manifest.json) records source URLs or repository paths,
hashes, transformations, and limitations for the files in [sources/](sources/).
Coastal records are illustrative; Harris County records are prepared extracts.
The additional NOAA, Census, IPCC, and BLS material consists of bounded historical
snapshots, not current local risk or employment assessments. Original downloads and
transcriptions are retained where needed to check provenance.

To add a case, add its source snapshot and manifest entry, then provide the input
and separate reference judgments in `dataset.json`. Review the expected verdicts
and explanation requirements before marking the case approved. Update the dataset
version and run the offline validator. Preserve previous runs so comparisons retain
the exact references they used. Add any new public files to the eval allowlist in
`.gitignore`; private data and experiment notes should remain local.

Keep related source families together if you later create a held-out set. Repeated
trials of familiar claims measure consistency; they are not independent new examples.
