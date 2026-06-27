import SwiftUI

struct SignalDetailView: View {
    let signal: Signal
    @EnvironmentObject private var appState: AppState
    @Environment(\.dismiss) private var dismiss

    @State private var quantity: String = "1"
    @State private var isExecuting = false
    @State private var executeResult: ExecuteResult?
    @State private var executeError: String?
    @State private var showExecuteConfirm = false

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 20) {

                    // Hero header
                    VStack(alignment: .leading, spacing: 8) {
                        HStack {
                            VStack(alignment: .leading, spacing: 4) {
                                Text(signal.ticker)
                                    .font(.system(.largeTitle, design: .rounded, weight: .bold))
                                    .foregroundStyle(.textPrimary)
                                HStack(spacing: 8) {
                                    ActionBadge(action: signal.action)
                                    StateBadge(state: signal.state)
                                }
                            }
                            Spacer()
                            ConfidenceDots(confidence: signal.confidence)
                        }
                        Text(signal.thesisSummary)
                            .font(.body)
                            .foregroundStyle(.textSecondary)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                    .padding(16)
                    .cardStyle()

                    // Key metrics
                    HStack(spacing: 12) {
                        MetricBlock(label: "ENTRY TARGET", value: signal.entryPriceTarget ?? "Market")
                        MetricBlock(label: "STOP RULE", value: signal.stopRule ?? "—")
                        MetricBlock(label: "HORIZON", value: signal.timeHorizon ?? "—")
                    }

                    // Quant score bar
                    if let score = signal.quantScore {
                        QuantScoreSection(score: score)
                    }

                    // Edge scorecard
                    if let ec = signal.edgeScorecard {
                        EdgeScorecardSection(scorecard: ec)
                    }

                    // What triggered this
                    if let trigger = signal.triggeredBy {
                        InfoSection(title: "Triggered by", text: trigger)
                    }

                    // Execute section
                    if signal.state.isActionable && appState.robinhoodLinked {
                        ExecuteSection(
                            signal: signal,
                            quantity: $quantity,
                            isExecuting: $isExecuting,
                            result: $executeResult,
                            error: $executeError,
                            showConfirm: $showExecuteConfirm
                        )
                    } else if signal.state.isActionable && !appState.robinhoodLinked {
                        ConnectBanner()
                    }

                    // Filled details
                    if signal.state == .filled, let price = signal.actualPrice {
                        FilledSection(signal: signal, price: price)
                    }

                    Spacer(minLength: 40)
                }
                .padding(16)
            }
            .background(Color.background.ignoresSafeArea())
            .navigationTitle(signal.ticker)
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button("Done") { dismiss() }
                        .foregroundStyle(.signalGreen)
                }
            }
            .confirmationDialog(
                "Execute \(signal.action.label) \(quantity) share(s) of \(signal.ticker)?",
                isPresented: $showExecuteConfirm,
                titleVisibility: .visible
            ) {
                Button("Confirm \(signal.action.label)", role: .destructive) { execute() }
                Button("Cancel", role: .cancel) {}
            }
        }
    }

    private func execute() {
        isExecuting = true
        executeError = nil
        Task {
            do {
                let result = try await APIClient.shared.executeSignal(
                    id: signal.id,
                    quantity: Double(quantity) ?? 1
                )
                await MainActor.run {
                    isExecuting = false
                    executeResult = result
                    if result.success {
                        UINotificationFeedbackGenerator().notificationOccurred(.success)
                    } else {
                        executeError = result.error ?? "Execution failed"
                        UINotificationFeedbackGenerator().notificationOccurred(.error)
                    }
                }
            } catch {
                await MainActor.run {
                    isExecuting = false
                    executeError = error.localizedDescription
                }
            }
        }
    }
}

// MARK: - Sub-sections

private struct MetricBlock: View {
    let label: String
    let value: String
    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(label)
                .font(.system(size: 9, weight: .semibold))
                .foregroundStyle(.textTertiary)
            Text(value)
                .font(.system(.subheadline, design: .monospaced, weight: .semibold))
                .foregroundStyle(.textPrimary)
                .lineLimit(2)
                .minimumScaleFactor(0.8)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(12)
        .cardStyle()
    }
}

private struct QuantScoreSection: View {
    let score: Double
    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("QUANT SCORE")
                .font(.caption.weight(.semibold))
                .foregroundStyle(.textTertiary)
            HStack(spacing: 12) {
                Text("\(Int(score))")
                    .font(.system(.title, design: .monospaced, weight: .bold))
                    .foregroundStyle(score >= 70 ? .signalGreen : score >= 45 ? .accent : .signalRed)
                VStack(alignment: .leading, spacing: 4) {
                    ProgressView(value: min(score / 100, 1))
                        .tint(score >= 70 ? .signalGreen : score >= 45 ? .accent : .signalRed)
                    Text(score >= 70 ? "Strong edge" : score >= 45 ? "Moderate edge" : "Weak — caution")
                        .font(.caption)
                        .foregroundStyle(.textSecondary)
                }
            }
        }
        .padding(16)
        .cardStyle()
    }
}

private struct EdgeScorecardSection: View {
    let scorecard: EdgeScorecard
    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("EDGE SCORECARD")
                .font(.caption.weight(.semibold))
                .foregroundStyle(.textTertiary)
            let items: [(String, Double?)] = [
                ("Moat", scorecard.moatScore),
                ("Timing", scorecard.timingScore),
                ("Risk/Reward", scorecard.riskRewardScore),
                ("Catalyst", scorecard.catalystStrength)
            ]
            ForEach(items, id: \.0) { name, value in
                if let v = value {
                    HStack {
                        Text(name).font(.subheadline).foregroundStyle(.textSecondary)
                        Spacer()
                        ProgressView(value: min(v / 100, 1))
                            .tint(.accent)
                            .frame(width: 80)
                        Text("\(Int(v))")
                            .font(.system(.caption, design: .monospaced))
                            .foregroundStyle(.textPrimary)
                            .frame(width: 30, alignment: .trailing)
                    }
                }
            }
        }
        .padding(16)
        .cardStyle()
    }
}

private struct InfoSection: View {
    let title: String
    let text: String
    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(title.uppercased())
                .font(.caption.weight(.semibold))
                .foregroundStyle(.textTertiary)
            Text(text)
                .font(.subheadline)
                .foregroundStyle(.textSecondary)
        }
        .padding(16)
        .cardStyle()
    }
}

private struct ExecuteSection: View {
    let signal: Signal
    @Binding var quantity: String
    @Binding var isExecuting: Bool
    @Binding var result: ExecuteResult?
    @Binding var error: String?
    @Binding var showConfirm: Bool

    var body: some View {
        VStack(spacing: 14) {
            if let result, result.success {
                HStack {
                    Image(systemName: "checkmark.circle.fill")
                        .foregroundStyle(.signalGreen)
                    Text("Order sent — ID \(result.orderId ?? "–")")
                        .font(.subheadline.weight(.semibold))
                        .foregroundStyle(.signalGreen)
                }
                .padding(14)
                .frame(maxWidth: .infinity)
                .cardStyle()
            } else {
                HStack(spacing: 12) {
                    VStack(alignment: .leading, spacing: 4) {
                        Text("SHARES")
                            .font(.system(size: 9, weight: .semibold))
                            .foregroundStyle(.textTertiary)
                        TextField("1", text: $quantity)
                            .keyboardType(.decimalPad)
                            .font(.system(.title3, design: .monospaced, weight: .semibold))
                            .foregroundStyle(.textPrimary)
                            .frame(width: 80)
                    }
                    .padding(12)
                    .cardStyle()

                    Button(action: { showConfirm = true }) {
                        Group {
                            if isExecuting {
                                ProgressView().tint(.black)
                            } else {
                                Text("Execute \(signal.action.label)")
                                    .font(.headline)
                            }
                        }
                        .frame(maxWidth: .infinity)
                        .padding(.vertical, 14)
                        .background(signal.action.isBullish ? Color.signalGreen : Color.signalRed)
                        .foregroundStyle(.black)
                        .clipShape(RoundedRectangle(cornerRadius: 12, style: .continuous))
                    }
                    .disabled(isExecuting)
                }

                if let error {
                    Text(error)
                        .font(.caption)
                        .foregroundStyle(.signalRed)
                }
            }
        }
    }
}

private struct ConnectBanner: View {
    var body: some View {
        HStack(spacing: 12) {
            Image(systemName: "link.circle")
                .font(.title2)
                .foregroundStyle(.accent)
            VStack(alignment: .leading, spacing: 2) {
                Text("Connect Robinhood to execute")
                    .font(.subheadline.weight(.semibold))
                    .foregroundStyle(.textPrimary)
                Text("Go to Settings to link your account")
                    .font(.caption)
                    .foregroundStyle(.textSecondary)
            }
        }
        .padding(16)
        .cardStyle()
    }
}

private struct FilledSection: View {
    let signal: Signal
    let price: Double
    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("FILL DETAILS")
                .font(.caption.weight(.semibold))
                .foregroundStyle(.textTertiary)
            HStack {
                MetricChip(label: "FILL PRICE", value: String(format: "$%.2f", price))
                if let size = signal.actualSize {
                    MetricChip(label: "SHARES", value: String(format: "%.4f", size))
                }
                if let modeled = signal.modeledFillPrice {
                    MetricChip(label: "MODELED", value: String(format: "$%.2f", modeled))
                }
            }
        }
        .padding(16)
        .cardStyle()
    }
}
