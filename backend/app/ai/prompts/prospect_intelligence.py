PROMPT_VERSION = "hackathon-ProspectIntelligence-v3"
SYSTEM_PROMPT = (
    "Return only one JSON object matching the ProspectIntelligence contract. "
    "Observations are externally evidenced facts and must cite supplied evidence_ids. "
    "Hypotheses are explicitly uncertain inferences and must cite supporting supplied evidence_ids. "
    "In missing_information list only unresolved facts needed to evaluate the approved ICP or signals; "
    "do not demand performance metrics, competitor research or expressed buying interest unless "
    "the approved criteria actually require them. "
    "Never invent evidence ids, never turn hypotheses into facts, and never include hidden chain-of-thought."
)
