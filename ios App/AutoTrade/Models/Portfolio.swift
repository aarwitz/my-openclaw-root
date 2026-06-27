import Foundation

struct Portfolio: Codable {
    let equity: Double
    let cash: Double
    let dayChange: Double
    let dayChangePct: Double
    let totalReturn: Double
    let totalReturnPct: Double
    let positions: [Position]
    let updatedAt: Date

    var totalValue: Double { equity + cash }
}

struct Position: Identifiable, Codable {
    let id: String       // instrument_id from Robinhood
    let ticker: String
    let name: String
    let quantity: Double
    let averageBuyPrice: Double
    let currentPrice: Double
    let equity: Double
    let percentChange: Double
    let totalReturn: Double
    let signalId: String?   // linked AutoTrade signal if followed from app

    var isGain: Bool { percentChange >= 0 }
}

struct PerformanceSummary: Codable {
    let totalSignals: Int
    let followedSignals: Int
    let winRate: Double          // 0.0–1.0
    let avgReturn: Double        // percent
    let bestTrade: TradeSummary?
    let worstTrade: TradeSummary?
    let periodDays: Int
}

struct TradeSummary: Codable {
    let ticker: String
    let returnPct: Double
    let closedAt: Date?
}
