import SwiftUI

struct SignalFeedView: View {
    @Binding var deepLinkedSignalId: String?
    @EnvironmentObject private var appState: AppState
    @State private var selectedSignal: Signal?
    @State private var filterState: FilterState = .active

    enum FilterState: String, CaseIterable {
        case active = "Active"
        case recent = "Recent Fills"
        case all    = "All"
    }

    var body: some View {
        NavigationStack {
            ZStack {
                Color.background.ignoresSafeArea()

                VStack(spacing: 0) {
                    // Filter picker
                    ScrollView(.horizontal, showsIndicators: false) {
                        HStack(spacing: 8) {
                            ForEach(FilterState.allCases, id: \.self) { f in
                                FilterChip(label: f.rawValue, isSelected: filterState == f) {
                                    withAnimation(.spring(response: 0.3)) { filterState = f }
                                }
                            }
                        }
                        .padding(.horizontal, 16)
                        .padding(.vertical, 12)
                    }

                    if appState.isLoadingSignals && appState.signals.isEmpty {
                        Spacer()
                        ProgressView()
                            .tint(.signalGreen)
                        Spacer()
                    } else if filteredSignals.isEmpty {
                        Spacer()
                        EmptySignalState(filter: filterState)
                        Spacer()
                    } else {
                        ScrollView {
                            LazyVStack(spacing: 10) {
                                ForEach(filteredSignals) { signal in
                                    SignalCard(signal: signal) {
                                        selectedSignal = signal
                                    }
                                    .padding(.horizontal, 16)
                                    .transition(.asymmetric(
                                        insertion: .move(edge: .top).combined(with: .opacity),
                                        removal: .opacity
                                    ))
                                }
                            }
                            .padding(.vertical, 8)
                        }
                        .refreshable { await appState.refreshSignals() }
                    }
                }
            }
            .navigationTitle("Signals")
            .navigationBarTitleDisplayMode(.large)
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button(action: { Task { await appState.refreshSignals() } }) {
                        Image(systemName: "arrow.clockwise")
                            .foregroundStyle(.textSecondary)
                    }
                }
            }
            .sheet(item: $selectedSignal) { signal in
                SignalDetailView(signal: signal)
            }
            .onChange(of: deepLinkedSignalId) { id in
                if let id, let match = appState.signals.first(where: { $0.id == id }) {
                    selectedSignal = match
                    deepLinkedSignalId = nil
                }
            }
        }
    }

    private var filteredSignals: [Signal] {
        switch filterState {
        case .active:
            return appState.activeSignals
        case .recent:
            return appState.recentFills
        case .all:
            return appState.signals
        }
    }
}

private struct FilterChip: View {
    let label: String
    let isSelected: Bool
    let action: () -> Void
    var body: some View {
        Button(action: action) {
            Text(label)
                .font(.subheadline.weight(isSelected ? .semibold : .regular))
                .padding(.horizontal, 14)
                .padding(.vertical, 7)
                .background(isSelected ? Color.signalGreen : Color.cardSurface)
                .foregroundStyle(isSelected ? .black : .textSecondary)
                .clipShape(Capsule())
        }
        .buttonStyle(.plain)
    }
}

private struct EmptySignalState: View {
    let filter: SignalFeedView.FilterState
    var body: some View {
        VStack(spacing: 16) {
            Image(systemName: "eye.slash")
                .font(.system(size: 40))
                .foregroundStyle(.textTertiary)
            Text(filter == .active ? "No active signals right now" : "No signals in this filter")
                .font(.headline)
                .foregroundStyle(.textSecondary)
            Text("The AI runs passes at market open, confirmation, and close.\nSignals appear here when they clear all risk gates.")
                .font(.subheadline)
                .foregroundStyle(.textTertiary)
                .multilineTextAlignment(.center)
                .padding(.horizontal, 40)
        }
    }
}
