from trader_state.enums import (
    CriticState,
    FalsifierSeverity,
    FalsifierStatus,
    HypothesisStatus,
    PositionStatus,
    RegimeSnapshotStatus,
    SignoffStatus,
    TradeIntentStatus,
)
from trader_state.models import Falsifier, Hypothesis, Position, RegimeSnapshot, TradeIntent


class TransitionError(ValueError):
    pass


def transition_hypothesis(hypothesis: Hypothesis, target_status: HypothesisStatus, falsifiers: list[Falsifier]) -> Hypothesis:
    if hypothesis.status == target_status.value:
        return hypothesis

    if hypothesis.status == HypothesisStatus.DRAFT.value and target_status == HypothesisStatus.ACTIVE:
        if not hypothesis.falsifier_ids:
            raise TransitionError("cannot activate hypothesis without falsifier_ids")
        if hypothesis.critic_state == CriticState.BLOCKED.value:
            raise TransitionError("cannot activate blocked hypothesis")
        return hypothesis.model_copy(update={"status": target_status.value, "version": hypothesis.version + 1})

    if hypothesis.status == HypothesisStatus.ACTIVE.value and target_status == HypothesisStatus.INVALIDATED:
        fatal_triggered = any(
            falsifier.id in hypothesis.falsifier_ids
            and falsifier.severity == FalsifierSeverity.FATAL.value
            and falsifier.status == FalsifierStatus.TRIGGERED.value
            for falsifier in falsifiers
        )
        if not fatal_triggered:
            raise TransitionError("cannot invalidate hypothesis without a triggered fatal falsifier")
        return hypothesis.model_copy(update={"status": target_status.value, "version": hypothesis.version + 1})

    if hypothesis.status == HypothesisStatus.ACTIVE.value and target_status == HypothesisStatus.RETIRED:
        return hypothesis.model_copy(update={"status": target_status.value, "version": hypothesis.version + 1})

    if hypothesis.status == HypothesisStatus.INVALIDATED.value and target_status == HypothesisStatus.ARCHIVED:
        return hypothesis.model_copy(update={"status": target_status.value, "version": hypothesis.version + 1})

    raise TransitionError(f"unsupported hypothesis transition: {hypothesis.status} -> {target_status.value}")


def transition_trade_intent(
    trade_intent: TradeIntent,
    target_status: TradeIntentStatus,
    hypothesis: Hypothesis,
    regime_snapshot: RegimeSnapshot,
) -> TradeIntent:
    if trade_intent.status == target_status.value:
        return trade_intent

    if trade_intent.status == TradeIntentStatus.DRAFT.value and target_status == TradeIntentStatus.PENDING_REVIEW:
        if hypothesis.status != HypothesisStatus.ACTIVE.value:
            raise TransitionError("trade intent requires an active hypothesis")
        if regime_snapshot.status != RegimeSnapshotStatus.ACTIVE.value:
            raise TransitionError("trade intent requires an active regime snapshot")
        if not trade_intent.sizing_basis.strip():
            raise TransitionError("trade intent requires sizing basis")
        return trade_intent.model_copy(update={"status": target_status.value, "version": trade_intent.version + 1})

    if trade_intent.status == TradeIntentStatus.PENDING_REVIEW.value and target_status == TradeIntentStatus.APPROVED:
        if trade_intent.critic_signoff_required and trade_intent.critic_signoff_status != SignoffStatus.APPROVED.value:
            raise TransitionError("trade intent missing required critic approval")
        return trade_intent.model_copy(update={"status": target_status.value, "version": trade_intent.version + 1})

    if trade_intent.status == TradeIntentStatus.PENDING_REVIEW.value and target_status == TradeIntentStatus.REJECTED:
        if trade_intent.critic_signoff_status not in {SignoffStatus.REJECTED.value}:
            raise TransitionError("trade intent reject requires rejected signoff status")
        return trade_intent.model_copy(update={"status": target_status.value, "version": trade_intent.version + 1})

    if trade_intent.status == TradeIntentStatus.APPROVED.value and target_status in {
        TradeIntentStatus.EXECUTED,
        TradeIntentStatus.CANCELED,
    }:
        return trade_intent.model_copy(update={"status": target_status.value, "version": trade_intent.version + 1})

    raise TransitionError(f"unsupported trade_intent transition: {trade_intent.status} -> {target_status.value}")


def transition_position(position: Position, target_status: PositionStatus) -> Position:
    if position.status == target_status.value:
        return position

    if position.status == PositionStatus.OPEN.value and target_status in {
        PositionStatus.TRIMMED,
        PositionStatus.CLOSED,
        PositionStatus.SUSPENDED,
    }:
        return position.model_copy(update={"status": target_status.value, "version": position.version + 1})

    raise TransitionError(f"unsupported position transition: {position.status} -> {target_status.value}")


def can_increase_position(existing_position: Position, proposed_quantity: float, trade_intent: TradeIntent) -> bool:
    if proposed_quantity <= existing_position.quantity:
        return True
    return (
        trade_intent.status == TradeIntentStatus.APPROVED.value
        and trade_intent.critic_signoff_required
        and trade_intent.critic_signoff_status == SignoffStatus.APPROVED.value
    )
