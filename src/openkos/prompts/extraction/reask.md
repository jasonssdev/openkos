A first extraction pass over the SOURCE below returned exactly ONE object, and that object only restates the source's own title. This is a narrow follow-up question, NOT a request to extract the source again: the object already kept is kept whatever you answer here.

Question: beyond the subject its title already names, does the BODY of this source develop any FURTHER distinct subject in its own right -- a specific idea, artifact, decision, procedure, person, organization, place, event, or project that the text says something substantive about?

Answer with ONLY those further subjects. Do NOT repeat the subject the title already names, and do NOT restate it under another name or another type: it is already kept, and returning it again adds nothing.

An empty array [] is a CORRECT and EXPECTED answer here. Many sources genuinely cover exactly one subject, and for those the first pass was right: answer [] and nothing else. Do not invent a subject, and do not promote a section heading, a passing mention, a supporting detail, or a step of something already kept in order to fill this answer. If you are unsure whether something is a separate subject or part of the one already kept, it is part of the one already kept -- answer [].

Vocabulary: each object's "type" MUST be one of exactly nine values: "Person", "Organization", "Place", "Event", "Procedure", "Decision", "Project", "Concept", or "Entity".

Return ONLY a JSON array, with NO prose, NO markdown, and NO code fences around it. Each element matches exactly this shape:
[{"type": "Person"|"Organization"|"Place"|"Event"|"Procedure"|"Decision"|"Project"|"Concept"|"Entity", "title": "...", "description": "...", "body": "..."}, ...]
Do NOT wrap the array in an outer object.