# Verifier prompt selection and results

Copyright (c) 2026 Amazon Web Services
SPDX-License-Identifier: MIT

The application selects **Verifier handoff candidate v1 with Sonnet 5**, using a
15,000-token response limit. It extends v4 with explicit checks of each claim's
citations, assumptions in gap notes, and actionable Analyst revision feedback.
The [selected prompt](prompts/verifier-handoff-candidate-v1.txt) matches the
evaluated candidate exactly.

## Selected candidate: November 2, 2026

The candidate and a fresh v4 control each ran the 12 reviewed cases three times,
using unchanged v1.2 references. A separate handoff comparison used the same
16 saved Analyst responses and 39 claims.

| Measure | V4 | Candidate v1 |
| --- | ---: | ---: |
| Regression verdicts matching reference v1.2 | 104/108 | 105/108 |
| Valid regression outputs | 36/36 | 36/36 |
| Handoff citation problems identified with a repair request | 0/4 | 3/4 |
| Targeted unsupported gap statements corrected or omitted | 0/4 | 1/4 |
| Unsupported time-window claim correctly handled | 0/1 | 0/1 |

The candidate introduced no new wrong regression verdicts at matched positions.
It requested three citation repairs; those repairs were not executed. Offline
orchestrator replay confirmed that two responses would trigger Analyst revision.
No live revision or synthesis was included in this comparison.

The candidate did not meet all handoff targets. The user selected it on
November 2 after reviewing these results as a practical improvement. Remaining
problems include a missed citation, unsupported gap assumptions and time-window
claims, and the IPCC confidence qualification missed in all three regression trials.
An assistant audit also found unsupported explanation details and spurious
contradictions. There is no complete explanation-quality score for this candidate.

These are development results, not held-out accuracy or full-pipeline results.
The handoff examples informed the prompt. The regression arms were not randomized
or matched by generation seed. Detailed runs and audit notes remain local.

## Historical v4 development results

The earlier saved v4 run used 12 cases, three trials per case, and 36 unique claims.
The verdicts below are rescored against the current v1.2 references.

| Measure | Earlier v4 baseline |
| --- | ---: |
| Valid final outputs | 36/36 |
| Verdicts matching reference v1.2 | 108/108 |
| Judge-rated explanation Pass | 98/108 |
| Judge-rated explanation Fail | 9/108 |
| Judge-rated explanation Needs review | 1/108 |

The explanation score uses the fixed Opus 5.5 judge and rubric v1.2. It combines
three new judgments after the IPCC reference correction with 105 previous grades
whose inputs were unchanged. It is not a fresh 108-call judge run.

## Limits of the historical result

The IPCC reference was corrected after reviewing the saved outputs. The perfect
saved verdict agreement is therefore not a held-out accuracy estimate. A later
three-trial check of the same v4 prompt returned the required Qualified status for
that IPCC claim only once; it omitted the material confidence qualification twice.
Confidence handling remains inconsistent.

Other explanation issues include unsupported additions, source-context assumptions,
and ambiguous completeness criteria. The judge also has disagreements with human
review. Correct verdicts and passing judge grades do not establish that every
explanation is reliable.

V4 was previously selected after comparing prompt revisions because it had the highest saved
verdict agreement and tied the best overall explanation Pass count. Later revisions
did not establish a clear overall improvement. The decision favors a practical
working baseline while retaining these limitations. It was superseded by the
candidate selection above.

These numbers describe recorded development experiments, not an expected score on
every rerun or evidence of full-pipeline quality. Use the [evaluation guide](README.md)
to measure changes in your own environment. Detailed experiment logs and learning
notes are kept outside the public suite.
