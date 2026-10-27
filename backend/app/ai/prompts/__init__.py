from dataclasses import dataclass

from app.ai.prompts import (
    closer,
    handoff,
    intent,
    prospect_intelligence,
    qualification,
    sales_playbook,
    setter,
)


@dataclass(frozen=True)
class PromptSpec:
    version: str
    system_prompt: str


PROMPTS = {
    "SalesPlaybookGeneration": PromptSpec(sales_playbook.PROMPT_VERSION, sales_playbook.SYSTEM_PROMPT),
    "ProspectIntelligence": PromptSpec(
        prospect_intelligence.PROMPT_VERSION, prospect_intelligence.SYSTEM_PROMPT
    ),
    "QualificationDecision": PromptSpec(qualification.PROMPT_VERSION, qualification.SYSTEM_PROMPT),
    "SetterDecision": PromptSpec(setter.PROMPT_VERSION, setter.SYSTEM_PROMPT),
    "IntentDecision": PromptSpec(intent.PROMPT_VERSION, intent.SYSTEM_PROMPT),
    "HandoffPackage": PromptSpec(handoff.PROMPT_VERSION, handoff.SYSTEM_PROMPT),
    "CloserDecision": PromptSpec(closer.PROMPT_VERSION, closer.SYSTEM_PROMPT),
}
