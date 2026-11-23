# Synthesizer evaluations

Copyright (c) 2026 Amazon Web Services
SPDX-License-Identifier: MIT

Six frozen cases evaluate the final answer from already reviewed claims. They cover
payment arithmetic, satellite measurement scope, Census margins of error, IPCC
qualifications, preliminary employment estimates, and conflicting fictional totals.
Five cases adapt reviewed Verifier fixtures; S006 is an explicitly fictional control.
New expectations were assistant-reviewed under standing approval before model calls,
not individually human-annotated.

## Validate and preview

From the repository root, using the locked project environment:

```bash
uv sync --locked --all-extras
source .venv/bin/activate
python evals/synthesizer/validate_dataset.py
python evals/run_stage.py synthesizer --case S001 --trials 1
```

Neither command makes model calls. [references.json](references.json) checks the
unchanged dataset and reused public Verifier source files. The fictional evidence
is embedded in S006. The original inputs and expected meanings have not been
rewritten for the promoted prompt.

## Run and review

With AWS credentials authorized to invoke the selected Bedrock model:

```bash
python evals/run_stage.py synthesizer --case S001 --trials 1 --execute
# All six cases, three repetitions each:
python evals/run_stage.py synthesizer --trials 3 --token-budget 100000 --execute
```

The runner calls the actual application Synthesizer, currently v4. Defaults use
the application's model and a 15,000-token response cap. Options `--model-id`,
`--max-tokens`, `--region`, and `--prompt-file` change only this experiment.
[prompt-v4.txt](prompt-v4.txt) preserves the selected prompt for explicit comparisons.
New runs use the installed locked SDK and record its version; historical scores
used Strands 1.59.0.

Outputs stay in private, Git-ignored `runs/synthesizer-eval-*` folders. Each includes
inputs, expected meanings for human review, the actual reviewed packet sent to the
model, its requested output schema, current code snapshots, prompt, usage,
application output, normalized output, and a `REVIEW.md` worksheet.

Reference expectations and case titles are withheld from the model. Reviewed claim
verdicts and reasons are legitimate stage inputs. The model receives the application's
`ReviewedContent` packet and returns a draft; application code assembles citation
metadata. The saved `output` is that application result, not a raw model-stream capture.

Use [RUBRIC.md](RUBRIC.md) and each case's `expected` requirements to grade the answer
and limitations. Contract checks validate format and traceability, not semantic
support. Grades remain pending until review. No Synthesizer LLM judge is claimed
or automatically invoked.

## Boundaries

These are normal, citation-closed inputs with at least one surviving claim. The
actual pipeline skips generated synthesis for all-rejected inputs; those are
rejected by this dataset validator. The suite does not evaluate upstream agents,
the revision loop, partial-answer behavior, safety gates, or end-to-end quality.

There are no harness retries. Model errors, timeouts, and unknown usage stop the
batch; invalid returned contracts are preserved and other cases continue. A single
agent invocation may use up to two model turns. Token budgets are checked between
calls and may be exceeded by the last call; token totals are not monetary costs.

[RESULTS.md](RESULTS.md) explains the v4 selection and remaining ambiguity. The small
development set is not a held-out accuracy benchmark. New results belong to a new
run, and changing reference meanings requires a versioned dataset. Historical
candidate runners, traces, and learning notes remain local; no paid experiment
was rerun for publication.
