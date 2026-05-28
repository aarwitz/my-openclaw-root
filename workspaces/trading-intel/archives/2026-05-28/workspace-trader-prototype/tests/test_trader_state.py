from datetime import date, datetime, timedelta
import unittest

from pydantic import ValidationError

from trader_state.enums import (
    ActorRole,
    ApprovalDecision,
    ApprovalStatus,
    ApprovalTargetObjectType,
    BreadthState,
    CheckFrequency,
    CriticState,
    CrowdingRisk,
    FalsifierSeverity,
    FalsifierStatus,
    HypothesisStatus,
    LiquidityProfile,
    LiquidityState,
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
from trader_state.models import (
    ApprovalRecord,
    Falsifier,
    Hypothesis,
    Position,
    RegimeSnapshot,
    ResearchCase,
    RiskPolicy,
    TradeIntent,
)
from trader_state.transitions import (
    TransitionError,
    can_increase_position,
    transition_hypothesis,
    transition_trade_intent,
)


def ts() -> datetime:
    return datetime(2026, 5, 28, 12, 0, 0)


def hypothesis_record(**overrides) -> Hypothesis:
    data = {
        "id": "HYPO-20260528-ABC",
        "created_at": ts(),
        "updated_at": ts(),
        "created_by": ActorRole.RESEARCHER,
        "updated_by": ActorRole.RESEARCHER,
        "version": 1,
        "audit_ref": "AUDIT-HYPO-1",
        "status": HypothesisStatus.DRAFT,
        "symbol_set": ["NVDA"],
        "title": "AI capex repricing",
        "thesis_type": ThesisType.LONG,
        "mispricing_claim": "Market underestimates durability of hyperscaler AI spend.",
        "why_now": "Consensus still frames this as a temporary spike.",
        "mechanism": "Forward estimates rerate as backlog converts.",
        "time_horizon_days": 180,
        "expected_move_base_pct": 18.0,
        "confidence_pct": 67.0,
        "regime_fit": RegimeFit.STRONG,
        "consensus_view": "AI cycle fades after near-term digestion.",
        "variant_perception": "Supply constraints defer rather than destroy demand.",
        "crowding_risk": CrowdingRisk.MEDIUM,
        "liquidity_profile": LiquidityProfile.DEEP,
        "invalidation_conditions": ["Cloud capex guides down two quarters in a row."],
        "supporting_evidence": ["earnings-call-2026-q1"],
        "falsifier_ids": ["FALS-1"],
        "critic_state": CriticState.APPROVED,
    }
    data.update(overrides)
    return Hypothesis(**data)


def falsifier_record(**overrides) -> Falsifier:
    data = {
        "id": "FALS-1",
        "created_at": ts(),
        "updated_at": ts(),
        "created_by": ActorRole.RESEARCHER,
        "updated_by": ActorRole.RESEARCHER,
        "version": 1,
        "audit_ref": "AUDIT-FALS-1",
        "status": FalsifierStatus.ACTIVE,
        "hypothesis_id": "HYPO-20260528-ABC",
        "severity": FalsifierSeverity.FATAL,
        "condition_text": "Capex collapses materially.",
        "observation_method": "Company guidance and filings.",
        "check_frequency": CheckFrequency.WEEKLY,
    }
    data.update(overrides)
    return Falsifier(**data)


def regime_record(**overrides) -> RegimeSnapshot:
    data = {
        "id": "REGIME-1",
        "created_at": ts(),
        "updated_at": ts(),
        "created_by": ActorRole.QUANT,
        "updated_by": ActorRole.QUANT,
        "version": 1,
        "audit_ref": "AUDIT-REGIME-1",
        "status": RegimeSnapshotStatus.ACTIVE,
        "as_of": ts(),
        "summary": "Neutral tape with adequate liquidity.",
        "liquidity_state": LiquidityState.NEUTRAL,
        "volatility_state": VolatilityState.NEUTRAL,
        "breadth_state": BreadthState.MIXED,
        "factor_leadership": ["quality", "growth"],
        "risk_posture": RiskPosture.NEUTRAL,
        "sizing_multiplier": 1.0,
    }
    data.update(overrides)
    return RegimeSnapshot(**data)


def trade_intent_record(**overrides) -> TradeIntent:
    data = {
        "id": "INTENT-1",
        "created_at": ts(),
        "updated_at": ts(),
        "created_by": ActorRole.TRADER,
        "updated_by": ActorRole.TRADER,
        "version": 1,
        "audit_ref": "AUDIT-INTENT-1",
        "status": TradeIntentStatus.DRAFT,
        "hypothesis_id": "HYPO-20260528-ABC",
        "regime_snapshot_id": "REGIME-1",
        "instrument": "NVDA",
        "direction": TradeDirection.LONG,
        "entry_logic": "Buy on pullback into support.",
        "entry_zone": "118-122",
        "stop_logic": "Exit below structural support with thesis damage.",
        "target_logic": "Trim into fair value at 145.",
        "max_loss_pct_portfolio": 2.5,
        "sizing_basis": "portfolio * 0.02 / stop_loss_pct",
        "expected_holding_period_days": 90,
        "critic_signoff_required": True,
        "critic_signoff_status": SignoffStatus.PENDING,
        "approval_record_ids": [],
    }
    data.update(overrides)
    return TradeIntent(**data)


def position_record(**overrides) -> Position:
    data = {
        "id": "POS-1",
        "created_at": ts(),
        "updated_at": ts(),
        "created_by": ActorRole.TRADER,
        "updated_by": ActorRole.TRADER,
        "version": 1,
        "audit_ref": "AUDIT-POS-1",
        "status": PositionStatus.OPEN,
        "hypothesis_id": "HYPO-20260528-ABC",
        "trade_intent_id": "INTENT-1",
        "instrument": "NVDA",
        "quantity": 100,
        "cost_basis": 120.0,
        "mark_price": 123.0,
        "gross_exposure": 12000.0,
        "net_exposure": 12000.0,
        "realized_pnl": 0.0,
        "unrealized_pnl": 300.0,
        "thesis_state": PositionThesisState.INTACT,
        "next_action_state": PositionActionState.HOLD,
        "last_review_at": ts(),
    }
    data.update(overrides)
    return Position(**data)


class TraderStateTests(unittest.TestCase):
    def test_hypothesis_confidence_out_of_range_fails(self) -> None:
        with self.assertRaises(ValidationError):
            hypothesis_record(confidence_pct=120)

    def test_triggered_falsifier_requires_evidence(self) -> None:
        with self.assertRaises(ValidationError):
            falsifier_record(status=FalsifierStatus.TRIGGERED)

    def test_hypothesis_activation_requires_non_blocked_critic_and_falsifier(self) -> None:
        with self.assertRaises(TransitionError):
            transition_hypothesis(
                hypothesis_record(falsifier_ids=[], critic_state=CriticState.BLOCKED),
                HypothesisStatus.ACTIVE,
                [],
            )

        activated = transition_hypothesis(hypothesis_record(), HypothesisStatus.ACTIVE, [falsifier_record()])
        self.assertEqual(activated.status, HypothesisStatus.ACTIVE.value)
        self.assertEqual(activated.version, 2)

    def test_hypothesis_invalidation_requires_triggered_fatal_falsifier(self) -> None:
        active_hypothesis = hypothesis_record(status=HypothesisStatus.ACTIVE)
        with self.assertRaises(TransitionError):
            transition_hypothesis(active_hypothesis, HypothesisStatus.INVALIDATED, [falsifier_record()])

        triggered = falsifier_record(
            status=FalsifierStatus.TRIGGERED,
            triggered_at=ts() + timedelta(days=1),
            trigger_evidence="warning-preannounce",
        )
        invalidated = transition_hypothesis(active_hypothesis, HypothesisStatus.INVALIDATED, [triggered])
        self.assertEqual(invalidated.status, HypothesisStatus.INVALIDATED.value)

    def test_trade_intent_pending_review_requires_active_inputs(self) -> None:
        with self.assertRaises(TransitionError):
            transition_trade_intent(
                trade_intent_record(),
                TradeIntentStatus.PENDING_REVIEW,
                hypothesis_record(status=HypothesisStatus.DRAFT),
                regime_record(),
            )

        pending_review = transition_trade_intent(
            trade_intent_record(),
            TradeIntentStatus.PENDING_REVIEW,
            hypothesis_record(status=HypothesisStatus.ACTIVE),
            regime_record(status=RegimeSnapshotStatus.ACTIVE),
        )
        self.assertEqual(pending_review.status, TradeIntentStatus.PENDING_REVIEW.value)

    def test_trade_intent_approval_requires_critic_approval(self) -> None:
        with self.assertRaises(TransitionError):
            transition_trade_intent(
                trade_intent_record(status=TradeIntentStatus.PENDING_REVIEW, critic_signoff_status=SignoffStatus.PENDING),
                TradeIntentStatus.APPROVED,
                hypothesis_record(status=HypothesisStatus.ACTIVE),
                regime_record(),
            )

        approved = transition_trade_intent(
            trade_intent_record(status=TradeIntentStatus.PENDING_REVIEW, critic_signoff_status=SignoffStatus.APPROVED),
            TradeIntentStatus.APPROVED,
            hypothesis_record(status=HypothesisStatus.ACTIVE),
            regime_record(),
        )
        self.assertEqual(approved.status, TradeIntentStatus.APPROVED.value)

    def test_position_increase_requires_approved_trade_intent(self) -> None:
        self.assertFalse(
            can_increase_position(
                position_record(),
                120,
                trade_intent_record(status=TradeIntentStatus.PENDING_REVIEW, critic_signoff_status=SignoffStatus.PENDING),
            )
        )
        self.assertTrue(
            can_increase_position(
                position_record(),
                120,
                trade_intent_record(status=TradeIntentStatus.APPROVED, critic_signoff_status=SignoffStatus.APPROVED),
            )
        )

    def test_risk_policy_range_validation(self) -> None:
        with self.assertRaises(ValidationError):
            RiskPolicy(
                id="POLICY-1",
                created_at=ts(),
                updated_at=ts(),
                created_by=ActorRole.AARON,
                updated_by=ActorRole.AARON,
                version=1,
                audit_ref="AUDIT-POLICY-1",
                status=RiskPolicyStatus.ACTIVE,
                portfolio_max_drawdown_pct=20,
                per_trade_max_loss_pct_min=3,
                per_trade_max_loss_pct_max=2,
                max_single_position_pct=15,
                max_theme_exposure_pct=30,
                liquidity_minimum_rule="Average daily dollar volume must exceed 20x intended trade size.",
                regime_gate_rules=["Reduce sizing in caution regime."],
                add_to_loser_rule="Only when pre-modeled, survivable, EV-improving, and critic-approved.",
                critic_override_rule="Aaron explicit approval required.",
            )

    def test_approval_record_conditional_requires_conditions(self) -> None:
        with self.assertRaises(ValidationError):
            ApprovalRecord(
                id="APPROVAL-1",
                created_at=ts(),
                updated_at=ts(),
                created_by=ActorRole.CRITIC,
                updated_by=ActorRole.CRITIC,
                version=1,
                audit_ref="AUDIT-APPROVAL-1",
                status=ApprovalStatus.ISSUED,
                target_object_type=ApprovalTargetObjectType.TRADE_INTENT,
                target_object_id="INTENT-1",
                reviewer_role=ReviewerRole.CRITIC,
                decision=ApprovalDecision.CONDITIONAL_APPROVE,
                reason="Needs tighter stop.",
                issued_at=ts(),
            )

    def test_research_case_supports_point_in_time_record(self) -> None:
        case = ResearchCase(
            id="CASE-1",
            created_at=ts(),
            updated_at=ts(),
            created_by=ActorRole.RESEARCHER,
            updated_by=ActorRole.RESEARCHER,
            version=1,
            audit_ref="AUDIT-CASE-1",
            status=ResearchCaseStatus.OPEN,
            case_type=ResearchCaseType.HISTORICAL_VALIDATION,
            as_of_date=date(2019, 1, 1),
            source_refs=["10-k-2018", "earnings-call-q4-2018"],
            timeline=["2018-12-31: data cut"],
            counterevidence=[],
            point_in_time_pass=True,
            conclusion="Case remains valid for historical replay.",
        )
        self.assertTrue(case.point_in_time_pass)


if __name__ == "__main__":
    unittest.main()
