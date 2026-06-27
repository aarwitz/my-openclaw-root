import SwiftUI

struct OnboardingView: View {
    @EnvironmentObject private var appState: AppState
    @State private var page = 0

    var body: some View {
        ZStack {
            Color.background.ignoresSafeArea()

            TabView(selection: $page) {
                IntroPage().tag(0)
                HowItWorksPage().tag(1)
                ConnectRobinhoodView(onComplete: finishOnboarding).tag(2)
            }
            .tabViewStyle(.page(indexDisplayMode: .never))
            .animation(.easeInOut, value: page)

            // Page dots
            VStack {
                Spacer()
                PageDots(count: 3, current: page)
                    .padding(.bottom, 40)
            }
        }
    }

    private func finishOnboarding() {
        appState.robinhoodLinked = true
        appState.isOnboarded = true
    }
}

private struct IntroPage: View {
    var body: some View {
        VStack(spacing: 32) {
            Spacer()
            // Logo mark
            ZStack {
                Circle()
                    .fill(Color.signalGreen.opacity(0.1))
                    .frame(width: 120, height: 120)
                Image(systemName: "bolt.fill")
                    .font(.system(size: 48, weight: .bold))
                    .foregroundStyle(.signalGreen)
            }

            VStack(spacing: 12) {
                Text("AutoTrade")
                    .font(.system(.largeTitle, design: .rounded, weight: .bold))
                    .foregroundStyle(.textPrimary)
                Text("AI-powered trading signals.\nYour Robinhood. Zero guesswork.")
                    .font(.title3)
                    .foregroundStyle(.textSecondary)
                    .multilineTextAlignment(.center)
            }

            VStack(spacing: 10) {
                FeatureRow(icon: "brain", text: "9-agent AI system analyzes the market every day")
                FeatureRow(icon: "checkmark.shield.fill", text: "Risk-gated — signals pass 10+ criteria before you see them")
                FeatureRow(icon: "bell.badge.fill", text: "Push alerts the moment a signal is live")
                FeatureRow(icon: "arrow.triangle.2.circlepath", text: "Auto-executes or manual — your choice")
            }
            .padding(.horizontal, 32)

            Spacer()
            Text("Swipe to continue")
                .font(.caption)
                .foregroundStyle(.textTertiary)
                .padding(.bottom, 80)
        }
        .padding(.horizontal, 24)
    }
}

private struct HowItWorksPage: View {
    var body: some View {
        VStack(spacing: 28) {
            Spacer()
            Text("How it works")
                .font(.system(.title, design: .rounded, weight: .bold))
                .foregroundStyle(.textPrimary)

            VStack(spacing: 20) {
                StepRow(number: "1", title: "AI scans the market", detail: "Researcher, Quant, and Critic agents score hundreds of setups every morning before open.")
                StepRow(number: "2", title: "Risk agent gates every trade", detail: "No signal ships without passing position limits, drawdown guards, and explainability checks.")
                StepRow(number: "3", title: "You get notified", detail: "Push alert lands on your phone with entry, stop, and the thesis in plain English.")
                StepRow(number: "4", title: "Execute with one tap", detail: "Review the signal and hit Execute — we route the order to your linked Robinhood account.")
            }
            .padding(.horizontal, 28)

            Spacer()
            Text("Swipe to connect Robinhood")
                .font(.caption)
                .foregroundStyle(.textTertiary)
                .padding(.bottom, 80)
        }
    }
}

private struct FeatureRow: View {
    let icon: String
    let text: String
    var body: some View {
        HStack(spacing: 12) {
            Image(systemName: icon)
                .font(.system(size: 16, weight: .semibold))
                .foregroundStyle(.signalGreen)
                .frame(width: 24)
            Text(text)
                .font(.subheadline)
                .foregroundStyle(.textSecondary)
            Spacer()
        }
    }
}

private struct StepRow: View {
    let number: String
    let title: String
    let detail: String
    var body: some View {
        HStack(alignment: .top, spacing: 16) {
            Text(number)
                .font(.system(.title3, design: .monospaced, weight: .bold))
                .foregroundStyle(.signalGreen)
                .frame(width: 28, alignment: .center)
            VStack(alignment: .leading, spacing: 4) {
                Text(title).font(.headline).foregroundStyle(.textPrimary)
                Text(detail).font(.subheadline).foregroundStyle(.textSecondary)
            }
        }
    }
}

private struct PageDots: View {
    let count: Int
    let current: Int
    var body: some View {
        HStack(spacing: 6) {
            ForEach(0..<count, id: \.self) { i in
                Capsule()
                    .fill(i == current ? Color.signalGreen : Color.textTertiary)
                    .frame(width: i == current ? 20 : 6, height: 6)
                    .animation(.spring(response: 0.3), value: current)
            }
        }
    }
}
