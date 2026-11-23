# Analyst evaluations

Copyright (c) 2026 Amazon Web Services
SPDX-License-Identifier: MIT

Eight frozen development cases test factual grounding, arithmetic, citation support,
coverage, scope, and evidence-gap handoff. A007/A008 are a paired control and misleading
source-profile challenge. These are four source families, not eight independent domains.

## Validate and preview

From the repository root, install the locked environment first:

```bash
uv sync --locked --all-extras
source .venv/bin/activate
python evals/analyst/validate_dataset.py
python evals/run_stage.py analyst --case A001 --trials 1
```

Validation and preview make no AWS/model calls. The original dataset, source snapshots,
reference approvals, and freeze record are included unchanged. Integrity checks validate
those references independently of the current application and SDK. The old application
hashes and Strands 1.59.0 recorded in the freeze describe the historical experiment;
they are not requirements for new runs.

## Run and review

With an AWS identity authorized to invoke the selected Bedrock model:

```bash
python evals/run_stage.py analyst --case A001 --trials 1 --execute
# All eight cases, three repetitions each:
python evals/run_stage.py analyst --trials 3 --token-budget 150000 --execute
```

The default is the current application Analyst prompt/model and a 10,000-token output
cap. Use `--model-id`, `--max-tokens`, `--region`, or `--prompt-file` to override this
experiment only. The two historical prompts are `analyst-prompt-baseline.txt` and
`analyst-prompt-v1.txt`; the original prompt was selected.

Each run creates a private `runs/analyst-eval-*` directory containing the run manifest,
selected cases, actual model payload/schema, outputs, usage, current code snapshots,
contract checks, and a `REVIEW.md` worksheet. Expected meanings and approval labels
are not sent to the Analyst. Inputs are already extracted evidence; this does not
exercise the extractor, revision loop, Verifier, final synthesis, or safety gate.

Read the output against its question, evidence, and `expected` requirements. Grade
Pass, Fail, or Needs review and give a reason. Include optional claims and gap notes:
a correct central answer can still contain an unsupported addition. Empty claims
are flagged separately, and semantic grades remain pending even when contracts pass.
The [five-check rubric](judge/rubric-v1.1.md) can also guide manual review.

## Optional LLM judge

Score saved outputs without rerunning the Analyst:

```bash
python evals/analyst/score_saved.py runs/analyst-eval-YOUR_RUN
python evals/analyst/score_saved.py runs/analyst-eval-YOUR_RUN --execute
```

The first command previews; the second makes paid calls using the reviewed v1.1 rubric
and the separate model in [judge-settings.json](judge-settings.json). Overrides
`--model-id` and `--region` affect only the judge. Evidence, the saved response, and
case requirements go to the judge; human grades, reviewer reasons, and Analyst prompt
identity do not. Mechanical failures, LLM grades, and combined grades are separate.
Judge outputs are saved in a new `runs/analyst-judge-*` directory; source outputs remain
unchanged. Review failures, ambiguous cases, and the judge's explanations yourself.

The published scorer runs on the current SDK. Historical calibration involved a small
selected development set, so its agreement numbers do not establish unattended judge
accuracy. The original calibration programs and private run history remain local.

## Limits and provenance

The runner makes no harness retries. Model errors, timeouts, or unknown usage stop the
batch; invalid returned contracts are recorded and the next case continues. Budgets
are checked between calls, so the last call can exceed the remaining token budget.
One agent invocation can use up to two model turns. Token totals are not dollar costs.
No fresh paid evaluation was performed to publish this suite.

[RESULTS.md](RESULTS.md) describes the historical prompt decision and limitations.
Changes to frozen inputs or reference meanings require a new dataset version.
Only the explicit public allowlist is checked in; generated outputs and learning
notes remain ignored. The application is not modified by evaluation overrides.
