import Foundation

// Maps to trade_intents JOIN hypotheses in trading-intel.sqlite
struct Signal: Identifiable, Codable, Equatable {
    let id: String
    let ticker: String
    let action: Action
    let vehicle: String        // "stock" | "call" | "put" | "etf"
    let state: State
    let confidence: Confidence
    let thesisSummary: String
    let timeHorizon: String?
    let entryPriceTarget: String?
    let stopRule: String?
    let quantScore: Double?
    let edgeScorecardJSON: String?
    let triggeredBy: String?
    let createdAt: Date
    let filledAt: Date?
    let actualPrice: Double?
    let actualSize: Double?
    let modeledFillPrice: Double?

    enum Action: String, Codable, CaseIterable {
        case open, add, trim, exit, rotate
        var label: String {
            switch self {
            case .open:   return "BUY"
            case .add:    return "ADD"
            case .trim:   return "TRIM"
            case .exit:   return "SELL"
            case .rotate: return "ROTATE"
            }
        }
        var isBullish: Bool { self == .open || self == .add }
    }

    enum State: String, Codable {
        case proposed, critic_review, risk_review, approved, blocked, submitted, filled, partial, canceled, rejected
        var isActionable: Bool { self == .approved }
        var isLive: Bool      { self == .submitted || self == .partial }
        var isClosed: Bool    { self == .filled || self == .canceled || self == .rejected }
        var displayLabel: String {
            switch self {
            case .approved:     return "Signal Live"
            case .submitted:    return "Order Sent"
            case .filled:       return "Filled"
            case .blocked:      return "Blocked"
            case .rejected:     return "Rejected"
            case .canceled:     return "Canceled"
            case .partial:      return "Partial Fill"
            case .critic_review: return "In Review"
            case .risk_review:  return "Risk Review"
            case .proposed:     return "Proposed"
            }
        }
    }

    enum Confidence: String, Codable {
        case low, medium, high
        var stars: Int {
            switch self { case .low: return 1; case .medium: return 2; case .high: return 3 }
        }
    }

    var edgeScorecard: EdgeScorecard? {
        guard let json = edgeScorecardJSON,
              let data = json.data(using: .utf8) else { return nil }
        return try? JSONDecoder().decode(EdgeScorecard.self, from: data)
    }
}

struct EdgeScorecard: Codable {
    let overallScore: Double?
    let moatScore: Double?
    let timingScore: Double?
    let riskRewardScore: Double?
    let catalystStrength: Double?
}

// What the user follows / executes
struct TradeAction: Identifiable, Codable {
    let id: String
    let signalId: String
    let ticker: String
    let action: Signal.Action
    let quantity: Double
    let priceAtAction: Double?
    let timestamp: Date
    let outcome: Outcome?

    enum Outcome: String, Codable {
        case win, loss, breakeven, open
    }
}
