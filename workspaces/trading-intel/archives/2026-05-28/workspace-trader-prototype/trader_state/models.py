from __future__ import annotations

from datetime import date, datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from trader_state.enums import (
    ActorRole,
    ApprovalDecision,
    ApprovalStatus,
    ApprovalTargetObjectType,
    AuditEventStatus,
    AuditEventType,
    BreadthState,
    CheckFrequency,
    CriticState,
    CrowdingRisk,
    FalsifierSeverity,
    FalsifierStatus,
    HypothesisStatus,
    LiquidityProfile,
    LiquidityState,
    PortfolioSnapshotStatus,
    PositionActionState,
    PositionStatus,
    PositionThesisState,
    RegimeFit,
    RegimeSnapshotStatus,
    ResearchCaseStatus,
    ResearchCaseType,
    ReviewerRole,
    RiskPolicyStatus,
    RiskPosture,
    SignoffStatus,
    ThesisType,
    TradeDirection,
    TradeIntentStatus,
    VolatilityState,
)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", use_enum_values=True)


class BaseRecord(StrictModel):
    id: str
    created_at: datetime
    updated_at: datetime
    created_by: ActorRole
    updated_by: ActorRole
    version: int = Field(ge=1)
    audit_ref: str

    @model_validator(mode="after")
    def validate_update_order(self) -> "BaseRecord":
        if self.updated_at < self.created_at:
            raise ValueError("updated_at must be >= created_at")
        return self


class RegimeSnapshot(BaseRecord):
    status: RegimeSnapshotStatus
    as_of: datetime
    summary: str
    liquidity_state: LiquidityState
    volatility_state: VolatilityState
    breadth_state: BreadthState
    factor_leadership: list[str]
    risk_posture: RiskPosture
    sizing_multiplier: float = Field(gt=0)
    disallowed_patterns: list[str] = Field(default_factory=list)
    notes: Optional[str] = None


class Hypothesis(BaseRecord):
    status: HypothesisStatus
    symbol_set: list[str] = Field(min_length=1)
    title: str
    thesis_type: ThesisType
    mispricing_claim: str
    why_now: str
    mechanism: str
    time_horizon_days: int = Field(gt=0)
    expected_move_base_pct: float
    expected_move_bull_pct: Optional[float] = None
    expected_move_bear_pct: Optional[float] = None
    confidence_pct: float = Field(ge=0, le=100)
    regime_fit: RegimeFit
    consensus_view: str
    variant_perception: str
    crowding_risk: CrowdingRisk
    liquidity_profile: LiquidityProfile
    invalidation_conditions: list[str] = Field(min_length=1)
    supporting_evidence: list[str] = Field(min_length=1)
    falsifier_ids: list[str] = Field(default_factory=list)
    critic_state: CriticState
    quant_score: Optional[float] = Field(default=None, ge=0, le=100)
    positioning_notes: Optional[str] = None
    next_review_at: Optional[datetime] = None


class ResearchCase(BaseRecord):
    status: ResearchCaseStatus
    case_type: ResearchCaseType
    hypothesis_id: Optional[str] = None
    as_of_date: date
    source_refs: list[str] = Field(min_length=1)
    timeline: list[str] = Field(min_length=1)
    counterevidence: list[str] = Field(default_factory=list)
    point_in_time_pass: bool
    conclusion: str


class Falsifier(BaseRecord):
    status: FalsifierStatus
    hypothesis_id: str
    severity: FalsifierSeverity
    condition_text: str
    observation_method: str
    check_frequency: CheckFrequency
    last_checked_at: Optional[datetime] = None
    triggered_at: Optional[datetime] = None
    trigger_evidence: Optional[str] = None

    @model_validator(mode="after")
    def validate_trigger_fields(self) -> "Falsifier":
        if self.status == FalsifierStatus.TRIGGERED.value:
            if not self.triggered_at or not self.trigger_evidence:
                raise ValueError("triggered falsifiers require triggered_at and trigger_evidence")
        return self


class TradeIntent(BaseRecord):
    status: TradeIntentStatus
    hypothesis_id: str
    regime_snapshot_id: str
    instrument: str
    direction: TradeDirection
    entry_logic: str
    entry_zone: str
    stop_logic: str
    target_logic: str
    max_loss_pct_portfolio: float = Field(gt=0, le=100)
    sizing_basis: str
    expected_holding_period_days: int = Field(gt=0)
    critic_signoff_required: bool
    critic_signoff_status: SignoffStatus
    approval_record_ids: list[str] = Field(default_factory=list)
    execution_constraints: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_signoff_consistency(self) -> "TradeIntent":
        if self.critic_signoff_required and self.critic_signoff_status == SignoffStatus.NOT_REQUESTED.value:
            raise ValueError("critic signoff cannot remain not_requested when signoff is required")
        return self


class Position(BaseRecord):
    status: PositionStatus
    hypothesis_id: str
    trade_intent_id: str
    instrument: str
    quantity: float
    cost_basis: float = Field(gt=0)
    mark_price: Optional[float] = Field(default=None, gt=0)
    gross_exposure: float = Field(ge=0)
    net_exposure: float
    realized_pnl: float
    unrealized_pnl: Optional[float] = None
    thesis_state: PositionThesisState
    next_action_state: PositionActionState
    last_review_at: datetime


class PortfolioSnapshot(BaseRecord):
    status: PortfolioSnapshotStatus
    as_of: datetime
    nav: float = Field(gt=0)
    cash_pct: float = Field(ge=0, le=100)
    gross_exposure_pct: float = Field(ge=0)
    net_exposure_pct: float
    max_drawdown_ytd_pct: float = Field(ge=0, le=100)
    ytd_return_pct: float
    spy_ytd_return_pct: float
    active_hypothesis_count: int = Field(ge=0)
    pending_trade_intent_count: int = Field(ge=0)
    largest_single_name_pct: float = Field(ge=0, le=100)
    largest_theme_pct: float = Field(ge=0, le=100)
    regime_snapshot_id: str


class RiskPolicy(BaseRecord):
    status: RiskPolicyStatus
    portfolio_max_drawdown_pct: float = Field(gt=0, le=100)
    per_trade_max_loss_pct_min: float = Field(gt=0, le=100)
    per_trade_max_loss_pct_max: float = Field(gt=0, le=100)
    max_single_position_pct: float = Field(gt=0, le=100)
    max_theme_exposure_pct: float = Field(gt=0, le=100)
    liquidity_minimum_rule: str
    regime_gate_rules: list[str] = Field(min_length=1)
    add_to_loser_rule: str
    critic_override_rule: str

    @model_validator(mode="after")
    def validate_trade_loss_range(self) -> "RiskPolicy":
        if self.per_trade_max_loss_pct_min > self.per_trade_max_loss_pct_max:
            raise ValueError("per_trade_max_loss_pct_min must be <= per_trade_max_loss_pct_max")
        return self


class ApprovalRecord(BaseRecord):
    status: ApprovalStatus
    target_object_type: ApprovalTargetObjectType
    target_object_id: str
    reviewer_role: ReviewerRole
    decision: ApprovalDecision
    reason: str
    conditions: list[str] = Field(default_factory=list)
    issued_at: datetime

    @model_validator(mode="after")
    def validate_conditions(self) -> "ApprovalRecord":
        if self.decision == ApprovalDecision.CONDITIONAL_APPROVE.value and not self.conditions:
            raise ValueError("conditional approvals require conditions")
        return self


class AuditEvent(StrictModel):
    id: str
    status: AuditEventStatus
    event_type: AuditEventType
    object_type: str
    object_id: str
    actor_role: ActorRole
    event_time: datetime
    field_changes: list[str] = Field(default_factory=list)
    reason: str
    source_refs: list[str] = Field(default_factory=list)


class SharedStateBundle(StrictModel):
    regime_snapshots: list[RegimeSnapshot] = Field(default_factory=list)
    hypotheses: list[Hypothesis] = Field(default_factory=list)
    research_cases: list[ResearchCase] = Field(default_factory=list)
    falsifiers: list[Falsifier] = Field(default_factory=list)
    trade_intents: list[TradeIntent] = Field(default_factory=list)
    positions: list[Position] = Field(default_factory=list)
    portfolio_snapshots: list[PortfolioSnapshot] = Field(default_factory=list)
    risk_policies: list[RiskPolicy] = Field(default_factory=list)
    approval_records: list[ApprovalRecord] = Field(default_factory=list)
    audit_events: list[AuditEvent] = Field(default_factory=list)
