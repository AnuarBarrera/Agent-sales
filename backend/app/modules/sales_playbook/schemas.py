from datetime import datetime
from enum import Enum
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictBool, model_validator

from app.modules.sales_playbook.commercial_context import CommercialReadiness
from app.modules.sales_playbook.models import SalesPlaybookSourceType, SalesPlaybookStatus

EXECUTABLE_PLAYBOOK_CONTRACT_VERSION = "2.0"
SEMANTIC_PLAYBOOK_CONTRACT_VERSION = "3.0"


class PlaybookMaturity(str, Enum):
    ESTABLISHED = "established"
    EXPERIMENTAL = "experimental"
    PENDING_VALIDATION = "pending_validation"


class PlaybookOrigin(str, Enum):
    MANUAL = "manual"
    QUESTIONNAIRE_PROPOSED = "questionnaire_proposed"
    MODEL_PROPOSED = "model_proposed"
    UNSPECIFIED = "unspecified"


class SalesChannel(str, Enum):
    WHATSAPP = "whatsapp"
    PHONE = "phone"
    VIDEO_CALL = "video_call"
    GOOGLE = "google"
    META = "meta"


class AudienceStyle(str, Enum):
    INDIVIDUAL = "individual"
    CLINIC = "clinic"
    BOTH = "both"


class ProspectClassification(str, Enum):
    ORO = "oro"
    AGENCIA_APARENTE = "agencia_aparente"
    INQUILINO = "inquilino"
    FRIO = "frio"


class AdsState(str, Enum):
    ACTIVE = "active"
    INACTIVE = "inactive"
    UNKNOWN = "unknown"


class WebState(str, Enum):
    FUNCTIONAL = "functional"
    BROKEN = "broken"
    ABSENT = "absent"
    UNKNOWN = "unknown"


class MarketplaceState(str, Enum):
    PAID_DEPENDENCY = "paid_dependency"
    PRESENT = "present"
    ABSENT = "absent"
    UNKNOWN = "unknown"


class WorkflowStageCode(str, Enum):
    SOURCING = "sourcing"
    ENRICHMENT = "enrichment"
    CLASSIFICATION = "classification"
    TOUCH_1 = "touch_1"
    TOUCH_1_5 = "touch_1_5"
    BRANCH_IDENTIFICATION = "branch_identification"
    TOUCH_2 = "touch_2"
    TOUCH_3 = "touch_3"
    PRE_CLOSE_CALL = "pre_close_call"
    EXPLORATION_CALL = "exploration_call"
    COMPLETE_ANALYSIS = "complete_analysis"
    PROPOSAL_CLOSING = "proposal_closing"
    FOLLOW_UP_GRACEFUL_CLOSE = "follow_up_graceful_close"


class WorkflowConditionCode(str, Enum):
    BOT_RESPONSE = "bot_response"
    HUMAN_FIRST = "human_first"
    WHATSAPP_NOT_SCHEDULED = "whatsapp_not_scheduled"
    NEGATIVE_RESPONSE = "negative_response"
    EXPLORATION_COMPLETED = "exploration_completed"
    CLEAR_INTEREST = "clear_interest"
    NO_RESPONSE = "no_response"
    FIT_CONFIRMED = "fit_confirmed"
    NO_FIT = "no_fit"
    CALL_SCHEDULED = "call_scheduled"


REQUIRED_WORKFLOW_CONDITIONS = frozenset({
    WorkflowConditionCode.BOT_RESPONSE,
    WorkflowConditionCode.HUMAN_FIRST,
    WorkflowConditionCode.WHATSAPP_NOT_SCHEDULED,
    WorkflowConditionCode.NEGATIVE_RESPONSE,
    WorkflowConditionCode.CLEAR_INTEREST,
    WorkflowConditionCode.EXPLORATION_COMPLETED,
})


class WorkflowOutcome(str, Enum):
    ADVANCE = "advance"
    BRANCH = "branch"
    WAIT = "wait"
    STOP = "stop"
    SHORTEN = "shorten"


class PlaybookRuleCode(str, Enum):
    ACTIVE_INVESTMENT_SIGNAL = "active_investment_signal"
    ADS_WEB_PRIORITY = "ads_web_priority"
    SCRAPER_ENRICH_BEFORE_CONTACT = "scraper_enrich_before_contact"
    SERVICE_ALERT_NOT_PITCH = "service_alert_not_pitch"
    MINIMUM_FRICTION_CTA = "minimum_friction_cta"
    NO_PREMATURE_PRODUCT_REVEAL = "no_premature_product_reveal"
    NO_PREMATURE_PRICING = "no_premature_pricing"
    NO_FALSE_FLATTERY = "no_false_flattery"
    NO_TECHNICAL_JARGON = "no_technical_jargon"
    ENTITY_LANGUAGE_ADAPTATION = "entity_language_adaptation"
    PRE_CLOSE_CONDITIONAL = "pre_close_conditional"
    NEGATIVE_GRACEFUL_CLOSE = "negative_graceful_close"
    COMPLETE_ANALYSIS_STAGE_GUARD = "complete_analysis_stage_guard"
    WHATSAPP_OPERATIONAL_LIMITS = "whatsapp_operational_limits"
    WHATSAPP_SENDING_WINDOWS = "whatsapp_sending_windows"
    WHATSAPP_DELAY = "whatsapp_delay"
    PERSONALIZATION_REQUIRED = "personalization_required"
    MAX_TWO_FOLLOWUPS = "max_two_followups"
    CLOSER_FIT_SIGNAL = "closer_fit_signal"
    CLOSER_NO_FIT_SIGNAL = "closer_no_fit_signal"
    OBJECTION_ISOLATION = "objection_isolation"
    SCHEDULE_EXPLORATION_CALL = "schedule_exploration_call"

    @classmethod
    def __get_pydantic_json_schema__(cls, core_schema, handler):
        schema = handler(core_schema)
        schema["description"] = (
            "Closed vocabulary for source-backed policy concepts. A code identifies the concept; "
            "the independent effect describes how the source treats it. Select a code only when "
            "the supplied source supports that concept."
        )
        schema["x-enum-descriptions"] = {
            code.value: description for code, description in PLAYBOOK_RULE_CODE_DESCRIPTIONS.items()
        }
        return schema


PLAYBOOK_RULE_CODE_DESCRIPTIONS: dict[PlaybookRuleCode, str] = {
    PlaybookRuleCode.ACTIVE_INVESTMENT_SIGNAL: (
        "Observable evidence that an organization is actively investing in customer acquisition or growth, "
        "rather than merely having an online profile."
    ),
    PlaybookRuleCode.ADS_WEB_PRIORITY: (
        "A prioritization concept based on the relationship between paid advertising activity and web presence "
        "or condition, rather than either signal considered alone."
    ),
    PlaybookRuleCode.SCRAPER_ENRICH_BEFORE_CONTACT: (
        "A sequencing concept for enriching a lead record with available information before outreach; it is "
        "distinct from deciding whether the lead qualifies."
    ),
    PlaybookRuleCode.SERVICE_ALERT_NOT_PITCH: (
        "An opening framed as notice of a verifiable service-related finding rather than presentation of a "
        "commercial offer; distinct from how much product detail is disclosed."
    ),
    PlaybookRuleCode.MINIMUM_FRICTION_CTA: (
        "A call to action seeking a small, easy response or permission to continue, rather than a high-commitment "
        "decision; distinct from the broader stage objective."
    ),
    PlaybookRuleCode.NO_PREMATURE_PRODUCT_REVEAL: (
        "A boundary concerning disclosure of product identity or solution details before the source-authorized "
        "point; distinct from describing a finding without presenting the offer."
    ),
    PlaybookRuleCode.NO_PREMATURE_PRICING: (
        "A boundary concerning when prices, fees, or package costs are introduced in a sequence; distinct from "
        "revealing product details without discussing price."
    ),
    PlaybookRuleCode.NO_FALSE_FLATTERY: (
        "A boundary concerning praise that is unsupported or generic, distinct from a specific observation "
        "grounded in available evidence."
    ),
    PlaybookRuleCode.NO_TECHNICAL_JARGON: (
        "A boundary concerning specialized or unnecessarily technical language that the intended audience may "
        "not readily understand, distinct from accurate plain-language explanation."
    ),
    PlaybookRuleCode.ENTITY_LANGUAGE_ADAPTATION: (
        "Adapting forms of address and wording to the identified recipient type, such as an individual or an "
        "organization, rather than using one form indiscriminately."
    ),
    PlaybookRuleCode.PRE_CLOSE_CONDITIONAL: (
        "A condition governing whether a preliminary close or rescue action is applicable, distinct from an "
        "unconditional workflow step."
    ),
    PlaybookRuleCode.NEGATIVE_GRACEFUL_CLOSE: (
        "A respectful end-of-contact behavior after a negative or declined response, distinct from continuing "
        "objection handling while the conversation remains active."
    ),
    PlaybookRuleCode.COMPLETE_ANALYSIS_STAGE_GUARD: (
        "A sequencing boundary connecting completion of an analysis stage to later workflow actions, distinct "
        "from the contents of the analysis itself."
    ),
    PlaybookRuleCode.WHATSAPP_OPERATIONAL_LIMITS: (
        "Limits on the volume or number of WhatsApp operations or contact attempts, distinct from allowed "
        "sending times and intervals between messages."
    ),
    PlaybookRuleCode.WHATSAPP_SENDING_WINDOWS: (
        "Time windows that govern when WhatsApp messages may be sent, distinct from volume limits or delays "
        "between messages."
    ),
    PlaybookRuleCode.WHATSAPP_DELAY: (
        "A timing interval or pause between WhatsApp communication events, distinct from permitted sending "
        "windows and overall attempt limits."
    ),
    PlaybookRuleCode.PERSONALIZATION_REQUIRED: (
        "A concept requiring outreach to use relevant information specific to the recipient, distinct from a "
        "generic message and from channel operating limits."
    ),
    PlaybookRuleCode.MAX_TWO_FOLLOWUPS: (
        "A numerical upper bound on follow-up attempts, distinct from the timing or content of each follow-up."
    ),
    PlaybookRuleCode.CLOSER_FIT_SIGNAL: (
        "Evidence or criteria indicating that a prospect fits the conditions for a later sales action, distinct "
        "from evidence indicating lack of fit."
    ),
    PlaybookRuleCode.CLOSER_NO_FIT_SIGNAL: (
        "Evidence or criteria indicating that a prospect does not fit the conditions for a later sales action, "
        "distinct from evidence indicating fit."
    ),
    PlaybookRuleCode.OBJECTION_ISOLATION: (
        "A method of identifying and addressing one objection at a time, distinct from responding to several "
        "unclarified concerns at once."
    ),
    PlaybookRuleCode.SCHEDULE_EXPLORATION_CALL: (
        "A workflow concept for arranging an exploratory conversation, distinct from a preliminary rescue call "
        "or from sending a proposal."
    ),
}


class PolicyRuleEffect(str, Enum):
    """Normative effect explicitly supported by a source; ``None`` is unspecified."""

    MUST = "must"
    MUST_NOT = "must_not"
    SHOULD = "should"
    MAY = "may"

    @classmethod
    def __get_pydantic_json_schema__(cls, core_schema, handler):
        schema = handler(core_schema)
        schema["description"] = (
            "Choose the effect from the source's explicit normativity. Effects are independent of the rule code. "
            "If a rule or effect is absent, its status is UNSPECIFIED; absence is not permission."
        )
        schema["x-effect-semantics"] = {
            "must": "The source explicitly requires the behavior or policy represented by the rule.",
            "must_not": "The source explicitly prohibits the behavior or policy represented by the rule.",
            "should": "The source explicitly recommends or prefers the behavior without making it mandatory.",
            "may": "The source explicitly permits the behavior within a stated scope; do not infer permission from silence.",
            "unspecified": "No rule or effect is stated by the source. Absence is not permission and is not MAY.",
        }
        return schema


class TemplateScenario(str, Enum):
    ORO = "oro"
    AGENCIA_APARENTE = "agencia_aparente"
    INQUILINO = "inquilino"
    FRIO = "frio"
    TOUCH_2_A = "touch_2_a"
    TOUCH_2_B = "touch_2_b"
    NEGATIVE_RESPONSE = "negative_response"
    OBJECTION = "objection"
    FOLLOW_UP = "follow_up"
    GRACEFUL_CLOSE = "graceful_close"


# The executable playbook stores the ordered, comma-separated Markdown section
# IDs that contributed source-derived policy.  The old 300-character bound was
# shorter than the real manual's 27 valid section IDs (3,236 characters).
PLAYBOOK_PROVENANCE_SOURCE_REF_MAX_LENGTH = 4096


class PlaybookProvenance(BaseModel):
    model_config = ConfigDict(extra="forbid")

    origin: PlaybookOrigin
    source_ref: str | None = Field(default=None, max_length=PLAYBOOK_PROVENANCE_SOURCE_REF_MAX_LENGTH)
    note: str | None = Field(default=None, max_length=1000)


class HumanReviewedProvenance(BaseModel):
    """Origin available only to v3 review edits, never provider extraction contracts."""

    model_config = ConfigDict(extra="forbid")

    origin: Literal["human_reviewed"] = "human_reviewed"
    decision_type: Literal["source_interpretation", "business_decision"] = "source_interpretation"
    source_ref: str | None = Field(default=None, max_length=PLAYBOOK_PROVENANCE_SOURCE_REF_MAX_LENGTH)
    note: str | None = Field(default=None, max_length=1000)
    reviewer_id: str | None = Field(default=None, min_length=1, max_length=64)
    reviewed_at: datetime | None = None


class PolicyRule(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: PlaybookRuleCode | None = None
    title: str = Field(..., min_length=1, max_length=300)
    description: str = Field(..., min_length=1, max_length=3000)
    applies_to: list[str] = Field(default_factory=list, max_length=10)
    priority: int | None = Field(default=None, ge=1, le=10)
    effect: PolicyRuleEffect | None = None
    condition: WorkflowConditionCode | None = None
    maturity: PlaybookMaturity = PlaybookMaturity.ESTABLISHED
    provenance: PlaybookProvenance


class WorkflowStageRule(PolicyRule):
    """Executable, source-backed rule attached to one workflow stage.

    Workflow rules use the existing ``PolicyRule`` identity and metadata.  A
    workflow rule is executable only when it has both a closed rule code and
    an explicit effect; provenance remains the existing domain provenance
    envelope.
    """

    code: PlaybookRuleCode
    effect: PolicyRuleEffect
    condition: WorkflowConditionCode | None = None


class WorkflowUnitRule(BaseModel):
    """Bounded source-backed rule. An absent rule is UNSPECIFIED, not permission."""

    model_config = ConfigDict(extra="forbid")

    code: PlaybookRuleCode
    title: str = Field(..., min_length=1, max_length=300)
    description: str = Field(..., min_length=1, max_length=3000)
    applies_to: list[str] = Field(default_factory=list, max_length=10)
    priority: int | None = Field(default=None, ge=1, le=10)
    effect: PolicyRuleEffect = Field(
        ..., description="Apply the source's explicit normativity; code identity does not determine effect."
    )
    condition: WorkflowConditionCode | None = Field(
        default=None,
        description="Include only an explicit source-backed condition; omission means no condition was specified.",
    )
    maturity: PlaybookMaturity = Field(
        ..., description="Preserve source-backed maturity; do not infer it from the rule code."
    )
    source_section_ids: list[str] = Field(
        ...,
        min_length=1,
        max_length=27,
        description="Only source section IDs assigned to the current unit may be referenced as support for this rule.",
    )


class TargetSegment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1, max_length=300)
    description: str = Field(..., min_length=1, max_length=2000)
    maturity: PlaybookMaturity = PlaybookMaturity.ESTABLISHED
    provenance: PlaybookProvenance


class Offer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary: str = Field(..., min_length=1, max_length=5000)
    price_context: str | None = Field(default=None, max_length=1000)


class StrategyPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_segments: list[TargetSegment] = Field(default_factory=list, max_length=20)
    offer: Offer
    positioning: list[str] = Field(default_factory=list, max_length=20)
    commercial_goal: str = Field(..., min_length=1, max_length=3000)
    qualification_principles: list[PolicyRule] = Field(default_factory=list, max_length=20)


class ClassificationRule(BaseModel):
    model_config = ConfigDict(extra="forbid")

    profile: ProspectClassification
    priority: int = Field(..., ge=1, le=10)
    ads_state: AdsState
    web_state: WebState
    marketplace_state: MarketplaceState
    requires_active_investment_signal: bool
    angle: str = Field(..., min_length=1, max_length=2000)
    maturity: PlaybookMaturity = PlaybookMaturity.ESTABLISHED
    provenance: PlaybookProvenance


class ProspectingPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sourcing_rules: list[PolicyRule] = Field(default_factory=list, max_length=20)
    classification_rules: list[ClassificationRule] = Field(..., min_length=4, max_length=20)
    prioritization_rules: list[PolicyRule] = Field(default_factory=list, max_length=20)


class TimingWindow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start: str = Field(..., pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    end: str = Field(..., pattern=r"^([01]\d|2[0-3]):[0-5]\d$")


class WorkflowStage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: WorkflowStageCode
    step_number: int = Field(..., ge=1, le=30)
    name: str = Field(..., min_length=1, max_length=300)
    objective: str = Field(..., min_length=1, max_length=2000)
    actions: list[str] = Field(default_factory=list, max_length=20)
    entry_conditions: list[WorkflowConditionCode] = Field(default_factory=list, max_length=10)
    exit_conditions: list[WorkflowConditionCode] = Field(default_factory=list, max_length=10)
    timing: str | None = Field(default=None, max_length=500)
    responsible_role: str | None = Field(default=None, max_length=100)
    rules: list[WorkflowStageRule] = Field(default_factory=list, max_length=20)
    maturity: PlaybookMaturity = PlaybookMaturity.ESTABLISHED
    provenance: PlaybookProvenance


class WorkflowTransition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    from_stage: WorkflowStageCode
    to_stage: WorkflowStageCode
    condition: WorkflowConditionCode
    outcome: WorkflowOutcome
    wait_hours: int | None = Field(default=None, ge=0, le=8760)
    description: str | None = Field(default=None, max_length=2000)
    maturity: PlaybookMaturity = PlaybookMaturity.ESTABLISHED
    provenance: PlaybookProvenance


class HumanReviewedWorkflowTransition(BaseModel):
    """Typed v3 review-supplied transition with separately auditable origin."""

    model_config = ConfigDict(extra="forbid")

    from_stage: WorkflowStageCode
    to_stage: WorkflowStageCode
    condition: WorkflowConditionCode
    outcome: WorkflowOutcome
    wait_hours: int | None = Field(default=None, ge=0, le=8760)
    description: str | None = Field(default=None, max_length=2000)
    maturity: PlaybookMaturity = PlaybookMaturity.ESTABLISHED
    provenance: HumanReviewedProvenance


class WorkflowPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    stages: list[WorkflowStage] = Field(..., min_length=13, max_length=30)
    transitions: list[WorkflowTransition] = Field(..., min_length=6, max_length=80)

    @model_validator(mode="after")
    def validate_workflow_references(self):
        codes = [stage.code for stage in self.stages]
        steps = [stage.step_number for stage in self.stages]
        if len(codes) != len(set(codes)):
            raise ValueError("workflow stages must have unique codes")
        if len(steps) != len(set(steps)):
            raise ValueError("workflow stages must have unique step numbers")
        if len(codes) != 13 or set(codes) != set(WorkflowStageCode):
            raise ValueError("workflow must contain the thirteen canonical stages")
        valid_codes = set(codes)
        for transition in self.transitions:
            if transition.from_stage not in valid_codes or transition.to_stage not in valid_codes:
                raise ValueError("workflow transition references an unknown stage")
        missing_conditions = REQUIRED_WORKFLOW_CONDITIONS - {transition.condition for transition in self.transitions}
        if missing_conditions:
            missing = ", ".join(sorted(condition.value for condition in missing_conditions))
            raise ValueError(f"workflow is missing required conditional transitions: {missing}")
        for stage in self.stages:
            seen_rules: dict[tuple[PlaybookRuleCode, WorkflowConditionCode | None], WorkflowStageRule] = {}
            for rule in stage.rules:
                identity = (rule.code, rule.condition)
                previous = seen_rules.get(identity)
                if previous is not None:
                    if previous.effect is not rule.effect:
                        raise ValueError(
                            f"workflow stage {stage.code.value} has conflicting rule effects: {rule.code.value}"
                        )
                    if previous.maturity is not rule.maturity:
                        raise ValueError(
                            f"workflow stage {stage.code.value} has conflicting rule maturity: {rule.code.value}"
                        )
                seen_rules[identity] = rule
        return self


class ReviewableWorkflowPolicy(BaseModel):
    """Non-authoritative v3 workflow shape that may omit conflict-backed stages."""

    model_config = ConfigDict(extra="forbid")

    stages: list[WorkflowStage] = Field(default_factory=list, max_length=30)
    transitions: list[WorkflowTransition | HumanReviewedWorkflowTransition] = Field(default_factory=list, max_length=80)

    @model_validator(mode="after")
    def validate_reviewable_workflow(self):
        codes = [stage.code for stage in self.stages]
        steps = [stage.step_number for stage in self.stages]
        if len(codes) != len(set(codes)):
            raise ValueError("workflow stages must have unique codes")
        if len(steps) != len(set(steps)):
            raise ValueError("workflow stages must have unique step numbers")
        valid_codes = set(codes)
        for transition in self.transitions:
            if transition.from_stage not in valid_codes or transition.to_stage not in valid_codes:
                raise ValueError("reviewable workflow transition references a missing stage")
        for stage in self.stages:
            seen_rules: dict[tuple[PlaybookRuleCode, WorkflowConditionCode | None], WorkflowStageRule] = {}
            for rule in stage.rules:
                identity = (rule.code, rule.condition)
                previous = seen_rules.get(identity)
                if previous is not None and (
                    previous.effect is not rule.effect or previous.maturity is not rule.maturity
                ):
                    raise ValueError(
                        f"workflow stage {stage.code.value} has conflicting rule authority: {rule.code.value}"
                    )
                seen_rules[identity] = rule
        return self


class ConditionRule(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: WorkflowConditionCode
    description: str = Field(..., min_length=1, max_length=2000)
    maturity: PlaybookMaturity = PlaybookMaturity.ESTABLISHED
    provenance: PlaybookProvenance


class DiscoveryQuestion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str = Field(..., min_length=1, max_length=1000)
    purpose: str = Field(..., min_length=1, max_length=1000)
    maturity: PlaybookMaturity = PlaybookMaturity.ESTABLISHED
    provenance: PlaybookProvenance


class ObjectionRule(PolicyRule):
    response_template_id: str | None = Field(default=None, max_length=100)


class SetterPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    objectives: list[str] = Field(default_factory=list, max_length=20)
    allowed_actions: list[str] = Field(default_factory=list, max_length=20)
    forbidden_rules: list[PolicyRule] = Field(..., min_length=5, max_length=30)
    language_rules: list[PolicyRule] = Field(..., min_length=1, max_length=20)
    handoff_conditions: list[ConditionRule] = Field(..., min_length=1, max_length=20)
    responsibilities: list[str] = Field(default_factory=list, max_length=20)


class CloserPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    objectives: list[str] = Field(default_factory=list, max_length=20)
    discovery_questions: list[DiscoveryQuestion] = Field(..., min_length=1, max_length=20)
    fit_signals: list[PolicyRule] = Field(..., min_length=1, max_length=20)
    no_fit_signals: list[PolicyRule] = Field(..., min_length=1, max_length=20)
    objection_rules: list[ObjectionRule] = Field(..., min_length=1, max_length=20)
    closing_rules: list[PolicyRule] = Field(..., min_length=1, max_length=20)
    responsibilities: list[str] = Field(default_factory=list, max_length=20)


class ChannelPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    channel: SalesChannel
    max_new_messages_per_day: int | None = Field(default=None, ge=1, le=1000)
    sending_windows: list[TimingWindow] = Field(default_factory=list, max_length=10)
    minimum_delay_seconds: int | None = Field(default=None, ge=0, le=86400)
    max_followups_per_lead: int | None = Field(default=None, ge=0, le=20)
    personalization_required: bool = False
    rules: list[PolicyRule] = Field(default_factory=list, max_length=30)


class PlaybookTemplate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(..., min_length=1, max_length=100, pattern=r"^[a-z0-9][a-z0-9_-]*$")
    purpose: str = Field(..., min_length=1, max_length=500)
    stage: WorkflowStageCode
    channel: SalesChannel
    scenario: TemplateScenario | None = None
    audience: AudienceStyle = AudienceStyle.BOTH
    text: str = Field(..., min_length=1, max_length=10000)
    required_variables: list[str] = Field(default_factory=list, max_length=20)
    conditions: list[WorkflowConditionCode] = Field(default_factory=list, max_length=10)
    maturity: PlaybookMaturity = PlaybookMaturity.ESTABLISHED
    provenance: PlaybookProvenance


class ExperimentVariant(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(..., min_length=1, max_length=100)
    description: str = Field(..., min_length=1, max_length=2000)
    template_id: str | None = Field(default=None, max_length=100)


class PlaybookExperiment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(..., min_length=1, max_length=100, pattern=r"^[a-z0-9][a-z0-9_-]*$")
    name: str = Field(..., min_length=1, max_length=300)
    variable: str = Field(..., min_length=1, max_length=500)
    variants: list[ExperimentVariant] = Field(..., min_length=2, max_length=5)
    success_metric: str = Field(..., min_length=1, max_length=500)
    maturity: Literal[PlaybookMaturity.EXPERIMENTAL, PlaybookMaturity.PENDING_VALIDATION] = PlaybookMaturity.EXPERIMENTAL
    provenance: PlaybookProvenance


class PendingDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str = Field(..., min_length=1, max_length=100, pattern=r"^[a-z0-9][a-z0-9_-]*$")
    question: str = Field(..., min_length=1, max_length=1000)
    options: list[str] = Field(default_factory=list, max_length=10)
    maturity: Literal[PlaybookMaturity.PENDING_VALIDATION] = PlaybookMaturity.PENDING_VALIDATION
    provenance: PlaybookProvenance


class SalesPlaybookContent(BaseModel):
    """Executable commercial policy. This is the only v2 playbook schema."""

    model_config = ConfigDict(extra="forbid")

    contract_version: Literal[EXECUTABLE_PLAYBOOK_CONTRACT_VERSION] = EXECUTABLE_PLAYBOOK_CONTRACT_VERSION
    schema_version: Literal[EXECUTABLE_PLAYBOOK_CONTRACT_VERSION] = EXECUTABLE_PLAYBOOK_CONTRACT_VERSION
    strategy: StrategyPolicy
    prospecting: ProspectingPolicy
    workflow: WorkflowPolicy
    setter_policy: SetterPolicy
    closer_policy: CloserPolicy
    channel_policies: list[ChannelPolicy] = Field(..., min_length=1, max_length=20)
    templates: list[PlaybookTemplate] = Field(..., min_length=1, max_length=100)
    experiments: list[PlaybookExperiment] = Field(..., min_length=1, max_length=20)
    pending_decisions: list[PendingDecision] = Field(..., min_length=1, max_length=20)

    @model_validator(mode="after")
    def validate_executable_shape(self):
        if self.contract_version != EXECUTABLE_PLAYBOOK_CONTRACT_VERSION:
            return self
        profiles = {item.profile for item in self.prospecting.classification_rules}
        if profiles != set(ProspectClassification):
            raise ValueError("prospecting must define all four canonical classifications")
        if not any(item.channel == SalesChannel.WHATSAPP for item in self.channel_policies):
            raise ValueError("an executable playbook requires a WhatsApp channel policy")
        return self


class SalesQuestionnaire(BaseModel):
    offer_summary: str = Field(..., min_length=1, max_length=5000)
    target_customer: str = Field(..., min_length=1, max_length=5000)
    problem_solved: str = Field(..., min_length=1, max_length=5000)
    market_location: str = Field(..., min_length=1, max_length=1000)
    price_context: str = Field(..., min_length=1, max_length=1000)
    differentiators: str = Field(..., min_length=1, max_length=5000)
    acquisition_method: str = Field(..., min_length=1, max_length=2000)
    first_contact_outcome: str = Field(..., min_length=1, max_length=2000)
    prohibited_behavior: str = Field(..., min_length=1, max_length=5000)
    prioritization_signals: str | None = Field(default=None, max_length=3000)
    follow_up_behavior: str | None = Field(default=None, max_length=3000)
    advance_conditions: str | None = Field(default=None, max_length=3000)
    stop_conditions: str | None = Field(default=None, max_length=3000)
    channel_constraints: str | None = Field(default=None, max_length=3000)
    setter_closer_responsibilities: str | None = Field(default=None, max_length=3000)


class SalesPlaybookDraftCreate(BaseModel):
    source_type: SalesPlaybookSourceType
    questionnaire: SalesQuestionnaire | None = None
    pasted_text: str | None = Field(default=None, min_length=1, max_length=100000)
    seller_profile_id: UUID | None = None
    source_document_ids: list[UUID] = Field(default_factory=list, max_length=10)
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")

    @model_validator(mode="after")
    def require_source_input(self):
        if self.source_type == SalesPlaybookSourceType.QUESTIONNAIRE:
            if self.questionnaire is None:
                raise ValueError("questionnaire is required for questionnaire drafts")
            if self.source_document_ids:
                raise ValueError("questionnaire drafts cannot include source documents")
        elif self.questionnaire is not None:
            raise ValueError("questionnaire answers cannot be combined with manual sources")
        if self.source_type != SalesPlaybookSourceType.QUESTIONNAIRE and self.source_document_ids and self.pasted_text:
            raise ValueError("Choose uploaded documents or pasted text, not both")
        if self.source_type in (SalesPlaybookSourceType.DOCUMENT, SalesPlaybookSourceType.MANUAL) and not self.pasted_text and not self.source_document_ids:
            raise ValueError("pasted_text is required for document or manual drafts")
        return self


class SalesPlaybookReject(BaseModel):
    reason: str | None = Field(default=None, min_length=1, max_length=5000)


class SalesPlaybookApprove(BaseModel):
    reviewed_guardrail_assertion_ids: list[str] = Field(default_factory=list)
    content_digest: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    confirmed: StrictBool = False


class SalesPlaybookWorkflowConflictResolve(BaseModel):
    candidate_id: str = Field(..., min_length=1, max_length=120)


class SalesPlaybookWorkflowTransitionComplete(BaseModel):
    """Human-supplied typed completion of one currently missing required transition."""

    model_config = ConfigDict(extra="forbid")

    from_stage: WorkflowStageCode
    condition: WorkflowConditionCode
    to_stage: WorkflowStageCode
    outcome: WorkflowOutcome
    maturity: PlaybookMaturity
    wait_hours: int | None = Field(default=None, ge=0, le=8760)
    description: str | None = Field(default=None, max_length=2000)
    source_section_ids: list[str] = Field(default_factory=list, max_length=27)

    @model_validator(mode="after")
    def validate_unique_sources(self):
        if len(self.source_section_ids) != len(set(self.source_section_ids)):
            raise ValueError("duplicate human transition source section")
        return self


class MissingRequiredTransition(BaseModel):
    condition: WorkflowConditionCode


class ApprovalReadinessBlocker(BaseModel):
    kind: Literal["workflow_conflict", "missing_required_transition", "safety", "extraction", "semantic_workflow", "source_conflict", "completeness"]
    identifier: str
    message: str | None = None


class ApprovalReadiness(BaseModel):
    blockers: list[str] = Field(default_factory=list)
    required_guardrail_assertion_ids: list[str] = Field(default_factory=list)
    blocker_details: list[ApprovalReadinessBlocker] = Field(default_factory=list)
    missing_required_transitions: list[MissingRequiredTransition] = Field(default_factory=list)
    ready_for_approval: bool


class SalesPlaybookRead(BaseModel):
    id: UUID
    workspace_id: UUID
    seller_profile_id: UUID | None
    version: int
    status: SalesPlaybookStatus
    source_type: SalesPlaybookSourceType
    structured_content: dict[str, Any]
    content_contract_version: str | None
    source_payload: dict[str, Any]
    model_name: str | None
    prompt_version: str | None
    generation_provider: str | None
    generation_operation: str | None
    generation_latency_ms: int | None
    generation_success: bool | None
    generation_tokens: int | None
    generation_audit: dict[str, Any] | None
    approved_by: UUID | None
    approved_at: datetime | None
    rejected_by: UUID | None
    rejected_at: datetime | None
    rejection_reason: str | None
    approval_readiness: ApprovalReadiness | None = None
    commercial_readiness: CommercialReadiness | None = None
    content_digest: str | None = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True, protected_namespaces=())
