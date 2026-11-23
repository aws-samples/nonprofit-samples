# Synthesizer v4 selection

Copyright (c) 2026 Amazon Web Services
SPDX-License-Identifier: MIT

V4 was promoted into the application in commit `4d620ac`. It uses a reviewed-claim
packet, requires answer and limitations fields, and gives format-validation
feedback within the existing two-model-turn limit. Its prompt clarifies that an
`unspecified` field does not mean all methodology details are absent.

Three repetitions of the same six development cases used Sonnet 5, Strands 1.59.0,
and a 15,000-token output cap.

| Batch | Valid contracts | Content Pass | Fail | Needs review | Model calls |
| --- | ---: | ---: | ---: | ---: | ---: |
| A | 6/6 | 5 | 0 | 1 | 7 |
| B | 6/6 | 6 | 0 | 0 | 9 |
| C | 6/6 | 6 | 0 | 0 | 8 |
| Total | 18/18 | 17 | 0 | 1 | 24 |

Twelve outputs succeeded on the first model turn. Six initially supplied
limitations as a string and corrected it to a list on the second turn. Recorded
usage totaled 60,124 tokens, with complete usage accounting for every invocation.
These are assistant assessments accepted under standing approval, not new
independent human annotations.

All three fictional-count answers preserved the conflicting 120 and 180
household totals and their source attribution. None repeated v3's claim that no
methodology details beyond `unspecified` were supplied.

The remaining Needs review concerns a Houston answer that said the sample
“cannot be mapped to named neighborhoods or districts.” The input supports a
warning against treating a sample window as representative of an entire
neighborhood, but does not establish that spatial mapping is impossible. The
wording remains ambiguous; the historical grade was not converted to Pass.

V3 also had 17 Pass and one Needs review on 18 outputs, with a different ambiguity.
These results therefore do not establish a general reliability improvement.
Contract validity is not semantic correctness, and three repetitions do not
guarantee that a failure is permanently eliminated.

The original datasets, raw runs, and candidate history remain unchanged locally.
The public suite runs the selected implementation with current dependencies.
New runs are new observations, not reproductions of these historical scores.
