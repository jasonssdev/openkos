You are analyzing one Decision recorded in a knowledge base.

Identify the ONE choice this Decision records:
- subject: a short noun phrase naming what was decided about (e.g. "billing tool", "release cadence").
- value: what was chosen for that subject, if the text states it plainly.
- evidence: one sentence copied VERBATIM from the Decision's body that supports the subject and value.

Reply with JSON only, no other text:
{"subject": "...", "value": "...", "evidence": "..."}

If no clear value is stated, set "value" to an empty string. If no single sentence supports the subject verbatim, set "evidence" to an empty string. Never paraphrase; "evidence" must be copied character-for-character from the body.