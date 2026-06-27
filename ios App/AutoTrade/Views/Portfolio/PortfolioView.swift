import SwiftUI

struct PortfolioView: View {
    @EnvironmentObject private var appState: AppState

    var body: some View {
        NavigationStack {
            ZStack {
                Color.background.ignoresSafeArea()
                if !appState.robinhoodLinked {
                    NotLinkedView()
                } else if appState.isLoadingPortfolio && appState.portfolio == nil {
                    ProgressView().tint(.signalGreen)
                } else {
                    ScrollView {
                        VStack(spacing: 16) {
                            if let portfolio = appState.portfolio {
                                PortfolioSummaryCard(portfolio: portfolio)
                            }
                            if let perf = appState.performance {
                                PerformanceCard(performance: perf)
                            }
                            if let positions = appState.portfolio?.positions, !positions.isEmpty {
                                PositionsSection(positions: positions)
                            }
                        }
                        .padding(16)
                    }
                    .refreshable { await appState.refreshPortfolio() }
                }
            }
            .navigationTitle("Portfolio")
            .navigationBarTitleDisplayMode(.large)
        }
    }
}

private struct PortfolioSummaryCard: View {
    let portfolio: Portfolio
    var body: some View {
        VStack(spacing: 16) {
            VStack(spacing: 4) {
                Text(String(format: "$%.2f", portfolio.totalValue))
                    .font(.system(.largeTitle, design: .rounded, weight: .bold))
                    .foregroundStyle(.textPrimary)
                HStack(spacing: 8) {
                    PriceDelta(value: portfolio.dayChange, showSign: true, suffix: "")
                    Text("·")
                        .foregroundStyle(.textTertiary)
                    PriceDelta(value: portfolio.dayChangePct, showSign: true, suffix: "%")
                    Text("today")
                        .font(.caption)
                        .foregroundStyle(.textTertiary)
                }
            }
            Divider().background(Color.cardBorder)
            HStack {
                SummaryItem(label: "Equity", value: String(format: "$%.2f", portfolio.equity))
                Spacer()
                SummaryItem(label: "Cash", value: String(format: "$%.2f", portfolio.cash))
                Spacer()
                SummaryItem(label: "Total Return", value: String(format: "%@%.2f%%", portfolio.totalReturnPct >= 0 ? "+" : "", portfolio.totalReturnPct), color: portfolio.totalReturnPct >= 0 ? .signalGreen : .signalRed)
            }
        }
        .padding(16)
        .cardStyle()
    }
}

private struct SummaryItem: View {
    let label: String
    let value: String
    var color: Color = .textPrimary
    var body: some View {
        VStack(spacing: 2) {
            Text(label).font(.caption).foregroundStyle(.textTertiary)
            Text(value).font(.subheadline.weight(.semibold)).foregroundStyle(color)
        }
    }
}

private struct PerformanceCard: View {
    let performance: PerformanceSummary
    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text("SIGNAL PERFORMANCE · \(performance.periodDays)D")
                .font(.caption.weight(.semibold))
                .foregroundStyle(.textTertiary)
            HStack(spacing: 0) {
                BigStat(label: "Win Rate", value: "\(Int(performance.winRate * 100))%", color: performance.winRate >= 0.5 ? .signalGreen : .signalRed)
                Divider().background(Color.cardBorder).frame(height: 40)
                BigStat(label: "Avg Return", value: String(format: "%@%.1f%%", performance.avgReturn >= 0 ? "+" : "", performance.avgReturn), color: performance.avgReturn >= 0 ? .signalGreen : .signalRed)
                Divider().background(Color.cardBorder).frame(height: 40)
                BigStat(label: "Followed", value: "\(performance.followedSignals)/\(performance.totalSignals)")
            }
        }
        .padding(16)
        .cardStyle()
    }
}

private struct BigStat: View {
    let label: String
    let value: String
    var color: Color = .textPrimary
    var body: some View {
        VStack(spacing: 4) {
            Text(value).font(.system(.title2, design: .rounded, weight: .bold)).foregroundStyle(color)
            Text(label).font(.caption).foregroundStyle(.textTertiary)
        }
        .frame(maxWidth: .infinity)
    }
}

private struct PositionsSection: View {
    let positions: [Position]
    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("OPEN POSITIONS")
                .font(.caption.weight(.semibold))
                .foregroundStyle(.textTertiary)
            ForEach(positions) { pos in
                PositionRow(position: pos)
            }
        }
    }
}

private struct PositionRow: View {
    let position: Position
    var body: some View {
        HStack {
            VStack(alignment: .leading, spacing: 2) {
                HStack(spacing: 6) {
                    Text(position.ticker)
                        .font(.headline.weight(.semibold))
                        .foregroundStyle(.textPrimary)
                    if position.signalId != nil {
                        Image(systemName: "bolt.fill")
                            .font(.system(size: 9))
                            .foregroundStyle(.signalGreen)
                    }
                }
                Text(String(format: "%.4f shares", position.quantity))
                    .font(.caption)
                    .foregroundStyle(.textSecondary)
            }
            Spacer()
            VStack(alignment: .trailing, spacing: 2) {
                Text(String(format: "$%.2f", position.equity))
                    .font(.subheadline.weight(.semibold))
                    .foregroundStyle(.textPrimary)
                PriceDelta(value: position.percentChange)
            }
        }
        .padding(14)
        .cardStyle()
    }
}

private struct NotLinkedView: View {
    var body: some View {
        VStack(spacing: 20) {
            Image(systemName: "link.badge.plus")
                .font(.system(size: 48))
                .foregroundStyle(.textTertiary)
            Text("Connect Robinhood")
                .font(.title2.weight(.bold))
                .foregroundStyle(.textPrimary)
            Text("Link your account in Settings to see your live portfolio and P&L.")
                .font(.subheadline)
                .foregroundStyle(.textSecondary)
                .multilineTextAlignment(.center)
                .padding(.horizontal, 40)
        }
    }
}
