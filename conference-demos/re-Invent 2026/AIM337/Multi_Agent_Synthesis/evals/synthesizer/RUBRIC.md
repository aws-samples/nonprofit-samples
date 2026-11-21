# Synthesizer review rubric

Copyright (c) 2026 Amazon Web Services
SPDX-License-Identifier: MIT

Use these five dimensions with the unchanged requirements in `dataset-v1.json`.
They summarize the criteria used before the original model calls.

1. **Answer and factual fidelity:** Answer the question, preserve numbers, units,
   periods, and measurement scope, and add no unsupported facts.
2. **Qualification retention:** Keep material conditions with the assertion in the
   answer. A generic disclaimer or separate limitations field does not repair an
   unqualified main assertion.
3. **Rejected assertions:** Do not endorse rejected claims. Explaining or negating
   a rejection is allowed; merely mentioning a rejected claim is not a failure.
4. **Citation grounding:** Cite evidence supporting the nearby surviving claims
   using exact bracketed IDs. An existing ID alone does not establish support.
   Several clearly scoped facts may share one citation.
5. **Gaps and contradictions:** Preserve supplied limitations and genuine unresolved
   conflict. Missing data is not proof of absence. Do not invent a reconciliation
   or treat every differing scenario as a contradiction.

Review both the answer and limitations. Grade each dimension Pass, Fail, Needs
review, or Not applicable, with a short explanation. Overall Pass requires all
required meanings and no material failed dimension. Any clear material failure
gives overall Fail; otherwise retain genuine uncertainty as Needs review.

Equivalent wording is acceptable. Style, claim order, and minor formatting
preferences do not fail an otherwise correct answer. Keep judgments separate from
raw outputs. Do not silently rewrite an answer or change its reference after
seeing a model response.
