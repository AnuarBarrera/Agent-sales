"""Deterministic semantic checks for executable sales playbooks.

These checks deliberately inspect only structured codes and required sections. They
are a safety net for the manual/extraction flow, not an LLM quality score.
"""

from pydantic import BaseModel, ConfigDict, Field

from app.modules.sales_playbook.schemas import (
    PlaybookOrigin,
    PlaybookRuleCode,
    PolicyRuleEffect,
    ProspectClassification,
    SalesChannel,
    SalesPlaybookContent,
    WorkflowConditionCode,
    WorkflowStageCode,
)


class SemanticCoverage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    complete: bool
    matched: list[str] = Field(default_factory=list)
    missing: list[str] = Field(default_factory=list)


def semantic_coverage(content: SalesPlaybookContent) -> SemanticCoverage:
    """Return a deterministic coverage report for the executable policy.

    The report is intentionally explainable: each missing item names a concrete
    structured requirement that a reviewer can repair in the source or draft.
    """

    matched: list[str] = []
    missing: list[str] = []

    def require(name: str, condition: bool) -> None:
        (matched if condition else missing).append(name)

    classifications = {item.profile for item in content.prospecting.classification_rules}
    require(
        "classification: oro/agencia_aparente/inquilino/frio",
        classifications == {
            ProspectClassification.ORO,
            ProspectClassification.AGENCIA_APARENTE,
            ProspectClassification.INQUILINO,
            ProspectClassification.FRIO,
        },
    )
    by_profile = {item.profile: item for item in content.prospecting.classification_rules}
    require("classification: oro priority 1", by_profile.get(ProspectClassification.ORO) is not None and by_profile[ProspectClassification.ORO].priority == 1)
    require(
        "classification: agencia/inquilino priority 2",
        all(by_profile.get(profile) is not None and by_profile[profile].priority == 2 for profile in (ProspectClassification.AGENCIA_APARENTE, ProspectClassification.INQUILINO)),
    )
    require("classification: frio priority 3", by_profile.get(ProspectClassification.FRIO) is not None and by_profile[ProspectClassification.FRIO].priority == 3)
    require(
        "classification: active investment signal",
        any(item.requires_active_investment_signal for item in content.prospecting.classification_rules),
    )

    stage_codes = {stage.code for stage in content.workflow.stages}
    require("workflow: thirteen canonical stages", stage_codes == set(WorkflowStageCode) and len(content.workflow.stages) == 13)
    transition_conditions = {transition.condition for transition in content.workflow.transitions}
    for condition in (
        WorkflowConditionCode.BOT_RESPONSE,
        WorkflowConditionCode.HUMAN_FIRST,
        WorkflowConditionCode.WHATSAPP_NOT_SCHEDULED,
        WorkflowConditionCode.NEGATIVE_RESPONSE,
        WorkflowConditionCode.CLEAR_INTEREST,
        WorkflowConditionCode.EXPLORATION_COMPLETED,
    ):
        require(f"workflow condition: {condition.value}", condition in transition_conditions)

    touch_1 = next((stage for stage in content.workflow.stages if stage.code == WorkflowStageCode.TOUCH_1), None)
    manual_workflow = touch_1 is not None and touch_1.provenance.origin is PlaybookOrigin.MANUAL
    if manual_workflow:
        touch_1_rules = {rule.code: rule for rule in touch_1.rules}
        require(
            "workflow touch_1: service alert must",
            touch_1_rules.get(PlaybookRuleCode.SERVICE_ALERT_NOT_PITCH) is not None
            and touch_1_rules[PlaybookRuleCode.SERVICE_ALERT_NOT_PITCH].effect is PolicyRuleEffect.MUST,
        )
        require(
            "workflow touch_1: no product reveal must_not",
            touch_1_rules.get(PlaybookRuleCode.NO_PREMATURE_PRODUCT_REVEAL) is not None
            and touch_1_rules[PlaybookRuleCode.NO_PREMATURE_PRODUCT_REVEAL].effect is PolicyRuleEffect.MUST_NOT,
        )
        require(
            "workflow touch_1: no pricing must_not",
            touch_1_rules.get(PlaybookRuleCode.NO_PREMATURE_PRICING) is not None
            and touch_1_rules[PlaybookRuleCode.NO_PREMATURE_PRICING].effect is PolicyRuleEffect.MUST_NOT,
        )
        require(
            "workflow touch_1: minimum friction CTA must",
            touch_1_rules.get(PlaybookRuleCode.MINIMUM_FRICTION_CTA) is not None
            and touch_1_rules[PlaybookRuleCode.MINIMUM_FRICTION_CTA].effect is PolicyRuleEffect.MUST,
        )

    rule_codes = _rule_codes(content)
    for code in (
        PlaybookRuleCode.SERVICE_ALERT_NOT_PITCH,
        PlaybookRuleCode.MINIMUM_FRICTION_CTA,
        PlaybookRuleCode.NO_PREMATURE_PRODUCT_REVEAL,
        PlaybookRuleCode.NO_PREMATURE_PRICING,
        PlaybookRuleCode.NO_FALSE_FLATTERY,
        PlaybookRuleCode.NO_TECHNICAL_JARGON,
        PlaybookRuleCode.ENTITY_LANGUAGE_ADAPTATION,
        PlaybookRuleCode.PRE_CLOSE_CONDITIONAL,
        PlaybookRuleCode.NEGATIVE_GRACEFUL_CLOSE,
        PlaybookRuleCode.COMPLETE_ANALYSIS_STAGE_GUARD,
    ):
        require(f"policy rule: {code.value}", code in rule_codes)

    whatsapp = next((item for item in content.channel_policies if item.channel == SalesChannel.WHATSAPP), None)
    require("channel: whatsapp policy", whatsapp is not None)
    require("channel: whatsapp limits/windows/delay", whatsapp is not None and bool(whatsapp.sending_windows) and whatsapp.minimum_delay_seconds is not None)
    require("channel: personalization and max two followups", whatsapp is not None and whatsapp.personalization_required and whatsapp.max_followups_per_lead == 2)

    require("setter: allowed actions", len(content.setter_policy.allowed_actions) >= 5)
    require("setter: handoff condition", bool(content.setter_policy.handoff_conditions))
    require("closer: fit/no-fit/objection/closing", bool(content.closer_policy.fit_signals) and bool(content.closer_policy.no_fit_signals) and bool(content.closer_policy.objection_rules) and bool(content.closer_policy.closing_rules))
    require("templates: first-class template", bool(content.templates))
    require("experiments: touch 2 A/B", any(len(item.variants) >= 2 and "touch" in item.id.lower() for item in content.experiments))
    require("pending decisions: field filter", bool(content.pending_decisions))

    return SemanticCoverage(complete=not missing, matched=matched, missing=missing)


def _rule_codes(content: SalesPlaybookContent) -> set[PlaybookRuleCode]:
    rules = [*content.strategy.qualification_principles, *content.prospecting.sourcing_rules, *content.prospecting.prioritization_rules]
    rules.extend(content.setter_policy.forbidden_rules)
    rules.extend(content.setter_policy.language_rules)
    rules.extend(content.closer_policy.fit_signals)
    rules.extend(content.closer_policy.no_fit_signals)
    rules.extend(content.closer_policy.objection_rules)
    rules.extend(content.closer_policy.closing_rules)
    for channel in content.channel_policies:
        rules.extend(channel.rules)
    for stage in content.workflow.stages:
        rules.extend(stage.rules)
    return {rule.code for rule in rules if rule.code is not None}
