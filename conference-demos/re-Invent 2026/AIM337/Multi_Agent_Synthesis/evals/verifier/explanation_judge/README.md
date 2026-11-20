# Judge the Verifier's explanations

Copyright (c) 2026 Amazon Web Services
SPDX-License-Identifier: MIT

This optional evaluator grades saved Verifier explanations using **Opus 5.5** and
[rubric v1.2](rubric-v1.2.md). It checks three things separately:

| Check | Question |
| --- | --- |
| Grounded | Are factual assertions in the explanation supported by the supplied evidence? |
| Complete | Does it cover the required reasoning and material caveats? |
| Supports verdict | Does the reasoning justify the status the Verifier returned? |

Each check returns Pass, Fail, or Needs review. Any Fail makes the overall grade
Fail; otherwise any Needs review is retained. An unsupported extra assertion can
fail grounding even when the status itself is justified.

## Score a saved Verifier run

Use the environment from the [Verifier guide](../README.md). Replace
`runs/YOUR_VERIFIER_RUN` with the output directory printed by `run_baseline.py`:

```bash
python evals/verifier/explanation_judge/score_saved.py \
  --source-run runs/YOUR_VERIFIER_RUN
```

This previews the plan without model calls. The scorer requires a completed run
whose outputs pass the verification contract. It checks the saved inputs, prompt,
reference coverage, and aggregate counts before preparing judge requests.

Add `--execute` to make one paid judge call per saved claim review:

```bash
python evals/verifier/explanation_judge/score_saved.py \
  --source-run runs/YOUR_VERIFIER_RUN --execute
```

A full 12-case, three-trial Verifier run produces 108 judge calls. The scorer does
not rerun the Verifier. Outputs go to a new local `runs/verifier-scorecard-*`
directory, including per-review grades, source hashes, and `summary.json`.

The judge receives the question, evidence, original claim statements, the Verifier's
status and reason, and required explanation meanings. Expected verdicts and human
explanation grades remain outside its input. It uses the reference requirements
captured in the selected run, not silently updated dataset labels.

Inspect every flagged review and a sample of passes. Judge failures can reveal
application mistakes or unclear evaluation criteria; resolve that distinction with
human review before selecting a prompt based on small score differences.

## Check the judge against human grades

The suite includes eight [calibration examples](calibration-v1.2.json) and ten
[diagnostic examples](validation-v1.2.json). Their approved grades, original inputs,
and review provenance are retained. They are development examples, including authored
controls and known missing evidence; they are not independent held-out accuracy tests.
Historical paths in their provenance identify the original records and are not
runtime dependencies.

Preview the calibration with the configured judge model:

```bash
python evals/verifier/explanation_judge/run_judge.py --trials 1
```

Select the diagnostic set explicitly:

```bash
python evals/verifier/explanation_judge/run_judge.py \
  --dataset evals/verifier/explanation_judge/validation-v1.2.json --trials 1
```

Add `--execute` for paid calls. With one trial, these commands make eight and ten
calls respectively. Inspect false passes, false failures, and each individual
check, as well as the overall grade. For offline tests only, use:

```bash
pytest evals/verifier/explanation_judge
```

## Configuration and limits

[judge-config.json](judge-config.json) records the model, region, 6,000-token response
cap, 120-second timeout, SDK version, and hashes of the rubric, runner, and shared
runtime. The saved-output scorer checks these hashes. When changing the judge,
review the configuration and rerun calibration before trusting comparisons.

The public packaging changes CLI defaults and requires an explicit saved-run path;
it preserves the evaluated rubric, typed judge payloads, and grading logic. Historical
validation results describe the earlier recorded runs, not new model executions of
the packaged code.

A failed call stops the batch, and partial results remain available. Budgets are
checked between calls, so the final call can exceed the remainder. Scoring a saved
run defaults to 600,000 tokens; calibration defaults to 100,000. Token usage and
latency are reported separately from explanation grades. No monetary cost is computed.
