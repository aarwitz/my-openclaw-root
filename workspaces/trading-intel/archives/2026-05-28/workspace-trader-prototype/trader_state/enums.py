from enum import Enum


class ActorRole(str, Enum):
    RESEARCHER = "researcher"
    CRITIC = "critic"
    QUANT = "quant"
    TRADER = "trader"
    SYSTEM = "system"
    AARON = "aaron"


class RegimeSnapshotStatus(str, Enum):
    DRAFT = "draft"
    ACTIVE = "active"
    SUPERSEDED = "superseded"


class LiquidityState(str, Enum):
    LOOSE = "loose"
    NEUTRAL = "neutral"
    TIGHT = "tight"


class VolatilityState(str, Enum):
    COMPRESSED = "compressed"
    NEUTRAL = "neutral"
    EXPANDED = "expanded"


class BreadthState(str, Enum):
    BROAD = "broad"
    MIXED = "mixed"
    NARROW = "narrow"


class RiskPosture(str, Enum):
    OFFENSE = "offense"
    NEUTRAL = "neutral"
    CAUTION = "caution"
    DEFENSE = "defense"


class HypothesisStatus(str, Enum):
    DRAFT = "draft"
    ACTIVE = "active"
    INVALIDATED = "invalidated"
    RETIRED = "retired"
    ARCHIVED = "archived"


class ThesisType(str, Enum):
    LONG = "long"
    SHORT = "short"
    PAIR = "pair"
    BASKET = "basket"
    WATCHLIST = "watchlist"


class RegimeFit(str, Enum):
    STRONG = "strong"
    ACCEPTABLE = "acceptable"
    WEAK = "weak"
    BLOCKED = "blocked"


class CrowdingRisk(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class LiquidityProfile(str, Enum):
    DEEP = "deep"
    ADEQUATE = "adequate"
    FRAGILE = "fragile"


class CriticState(str, Enum):
    NOT_REVIEWED = "not_reviewed"
    CHALLENGED = "challenged"
    APPROVED = "approved"
    BLOCKED = "blocked"


class ResearchCaseStatus(str, Enum):
    OPEN = "open"
    COMPLETE = "complete"
    STALE = "stale"
    REJECTED = "rejected"


class ResearchCaseType(str, Enum):
    LIVE = "live"
    HISTORICAL_VALIDATION = "historical_validation"
    POSTMORTEM = "postmortem"


class FalsifierStatus(str, Enum):
    ACTIVE = "active"
    TRIGGERED = "triggered"
    DISMISSED = "dismissed"
    RETIRED = "retired"


class FalsifierSeverity(str, Enum):
    WARNING = "warning"
    MAJOR = "major"
    FATAL = "fatal"


class CheckFrequency(str, Enum):
    DAILY = "daily"
    WEEKLY = "weekly"
    EVENT_DRIVEN = "event_driven"


class TradeIntentStatus(str, Enum):
    DRAFT = "draft"
    PENDING_REVIEW = "pending_review"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXECUTED = "executed"
    CANCELED = "canceled"


class TradeDirection(str, Enum):
    LONG = "long"
    SHORT = "short"


class SignoffStatus(str, Enum):
    NOT_REQUESTED = "not_requested"
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class PositionStatus(str, Enum):
    OPEN = "open"
    TRIMMED = "trimmed"
    CLOSED = "closed"
    SUSPENDED = "suspended"


class PositionThesisState(str, Enum):
    INTACT = "intact"
    WEAKENED = "weakened"
    BROKEN = "broken"


class PositionActionState(str, Enum):
    HOLD = "hold"
    TRIM = "trim"
    ADD_REVIEW = "add_review"
    EXIT_REVIEW = "exit_review"


class PortfolioSnapshotStatus(str, Enum):
    FINAL = "final"


class RiskPolicyStatus(str, Enum):
    DRAFT = "draft"
    ACTIVE = "active"
    RETIRED = "retired"


class ApprovalStatus(str, Enum):
    ISSUED = "issued"
    SUPERSEDED = "superseded"


class ApprovalTargetObjectType(str, Enum):
    HYPOTHESIS = "hypothesis"
    TRADE_INTENT = "trade_intent"
    POSITION_CHANGE = "position_change"
    REGIME_SNAPSHOT = "regime_snapshot"


class ReviewerRole(str, Enum):
    CRITIC = "critic"
    AARON = "aaron"


class ApprovalDecision(str, Enum):
    APPROVE = "approve"
    REJECT = "reject"
    BLOCK = "block"
    CONDITIONAL_APPROVE = "conditional_approve"


class AuditEventStatus(str, Enum):
    RECORDED = "recorded"


class AuditEventType(str, Enum):
    CREATE = "create"
    UPDATE = "update"
    REVIEW = "review"
    APPROVAL = "approval"
    EXECUTION = "execution"
    INVALIDATION = "invalidation"
    OVERRIDE = "override"
