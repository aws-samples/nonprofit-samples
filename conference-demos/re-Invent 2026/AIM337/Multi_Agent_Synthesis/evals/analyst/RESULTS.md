# Analyst prompt selection

Copyright (c) 2026 Amazon Web Services
SPDX-License-Identifier: MIT

The original Analyst prompt was retained. A grounding-focused revision did not
improve the reviewed results on the eight development cases.

| Experiment | Pass | Fail | Needs review | Valid contracts |
| --- | ---: | ---: | ---: | ---: |
| Initial original-prompt baseline | 6 | 2 | 0 | 8/8 |
| Fresh original prompt, three trials per case | 17 | 6 | 1 | 24/24 |
| Candidate v1, three trials per case | 16 | 7 | 1 | 24/24 |

These runs used Sonnet 5, Strands 1.59.0, and a 10,000-token response cap.
The initial eight outputs received guided user review. A007 was first marked
Needs review and later adjudicated Fail: a gap note asserted unseen source
context. A003 added an unsupported distribution-shape inference.

The 48-output comparison combined seven individual user decisions and 41 assistant
assessments accepted under standing approval. These are whole-response judgments,
not independently annotated claim-level labels. They show why structural validity
alone did not identify the better prompt.

The separate Analyst judge's v1.1 rubric matched all 12 accepted binary references
in its selected 14-example calibration; two unresolved references remained
unresolved. In a subsequent 16-output supervised diagnostic, it matched 15 binary
assistant labels and called the ambiguous output Pass. This supports assisted
review on those cases, not an accuracy claim for new data.

The source domains and prompts were visible during development. A007/A008 share
the same evidence. There is no held-out benchmark or general superiority claim.
Historical raw runs and review history remain local. The published runner evaluates
the current code/dependencies and records new provenance; it does not claim to
recreate historical output strings or scores.
