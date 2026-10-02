You are a selection step in a local-first knowledge engine. Below is a SOURCE text and a CLOSED list of candidate derived objects that two extraction passes already proposed for it. Some candidates may be genuine distinct subjects the source is about; others may be decayed near-duplicates, shallow facets of a genuine subject, or over-eager enumeration.

Decide which candidates are GENUINE, distinct subjects worth keeping as standalone derived objects. Prefer FEWER, RICHER objects over many shallow ones -- the same restraint the extraction step itself was asked to apply.

A candidate whose title names the source document, meeting, or gathering itself -- a container or framing title for the WHOLE source rather than a subject the source discusses -- is NEVER a genuine subject. Always drop it, even when no other candidate covers its content. Concrete decisions, commitments, and named topics the source records are worth keeping AHEAD of any summary-of-the-source candidate.

You MUST select ONLY from the candidate titles given below -- you MUST NOT invent, rename, or rephrase a title. Echo each kept title EXACTLY as it appears in the candidate list.

Return ONLY a JSON object, with NO prose, NO markdown, and NO code fences around it, in exactly this shape: {"keep": ["<exact candidate title>", ...]}. Do NOT wrap it in an array, and do NOT return a bare array of titles.