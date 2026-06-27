import SwiftUI

struct SignalCard: View {
    let signal: Signal
    var onTap: (() -> Void)? = nil

    var body: some View {
        Button(action: { onTap?() }) {
            VStack(alignment: .leading, spacing: 12) {
                // Header row
                HStack(alignment: .top) {
                    VStack(alignment: .leading, spacing: 2) {
                        HStack(spacing: 6) {
                            Text(signal.ticker)
                                .font(.system(.title2, design: .rounded, weight: .bold))
                                .foregroundStyle(.textPrimary)
                            Text(signal.vehicle.uppercased())
                                .font(.caption2.weight(.semibold))
                                .padding(.horizontal, 6)
                                .padding(.vertical, 2)
                                .background(Color.accent.opacity(0.2))
                                .foregroundStyle(.accent)
                                .clipShape(Capsule())
                        }
                        Text(signal.timeHorizon ?? "—")
                            .font(.caption)
                            .foregroundStyle(.textSecondary)
                    }
                    Spacer()
                    VStack(alignment: .trailing, spacing: 4) {
                        ActionBadge(action: signal.action)
                        StateBadge(state: signal.state)
                    }
                }

                // Thesis
                Text(signal.thesisSummary)
                    .font(.subheadline)
                    .foregroundStyle(.textSecondary)
                    .lineLimit(2)

                // Footer metrics
                HStack(spacing: 16) {
                    if let entry = signal.entryPriceTarget {
                        MetricChip(label: "ENTRY", value: entry)
                    }
                    if let stop = signal.stopRule {
                        MetricChip(label: "STOP", value: stop)
                    }
                    Spacer()
                    ConfidenceDots(confidence: signal.confidence)
                }

                if let score = signal.quantScore {
                    ProgressView(value: min(score / 100, 1))
                        .tint(scoreColor(score))
                        .scaleEffect(y: 0.6)
                }
            }
            .padding(16)
            .cardStyle()
        }
        .buttonStyle(.plain)
    }

    private func scoreColor(_ score: Double) -> Color {
        score >= 70 ? .signalGreen : score >= 45 ? .accent : .signalRed
    }
}

struct ActionBadge: View {
    let action: Signal.Action
    var body: some View {
        Text(action.label)
            .font(.caption.weight(.bold))
            .padding(.horizontal, 8)
            .padding(.vertical, 3)
            .background(action.isBullish ? Color.signalGreen.opacity(0.15) : Color.signalRed.opacity(0.15))
            .foregroundStyle(action.isBullish ? .signalGreen : .signalRed)
            .clipShape(Capsule())
    }
}

struct StateBadge: View {
    let state: Signal.State
    var body: some View {
        HStack(spacing: 4) {
            Circle()
                .fill(stateColor)
                .frame(width: 6, height: 6)
                .opacity(state.isActionable ? 1 : 0.5)
            Text(state.displayLabel)
                .font(.caption2.weight(.medium))
                .foregroundStyle(.textSecondary)
        }
    }
    private var stateColor: Color {
        switch state {
        case .approved:  return .signalGreen
        case .submitted, .partial: return .accent
        case .filled:    return .textSecondary
        case .blocked, .rejected, .canceled: return .signalRed
        default:         return .textTertiary
        }
    }
}

struct MetricChip: View {
    let label: String
    let value: String
    var body: some View {
        VStack(alignment: .leading, spacing: 1) {
            Text(label)
                .font(.system(size: 9, weight: .semibold))
                .foregroundStyle(.textTertiary)
            Text(value)
                .font(.system(.caption, design: .monospaced, weight: .medium))
                .foregroundStyle(.textPrimary)
                .lineLimit(1)
        }
    }
}

struct ConfidenceDots: View {
    let confidence: Signal.Confidence
    var body: some View {
        HStack(spacing: 3) {
            ForEach(1...3, id: \.self) { i in
                Circle()
                    .fill(i <= confidence.stars ? Color.signalGreen : Color.textTertiary)
                    .frame(width: 7, height: 7)
            }
        }
    }
}
