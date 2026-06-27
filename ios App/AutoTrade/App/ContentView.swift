import SwiftUI

struct ContentView: View {
    @EnvironmentObject private var appState: AppState
    @State private var selectedTab: Tab = .signals
    @State private var deepLinkedSignalId: String?

    enum Tab: Hashable { case signals, portfolio, settings }

    var body: some View {
        TabView(selection: $selectedTab) {
            SignalFeedView(deepLinkedSignalId: $deepLinkedSignalId)
                .tabItem { Label("Signals", systemImage: "bolt.fill") }
                .tag(Tab.signals)
                .badge(appState.newSignalCount > 0 ? appState.newSignalCount : 0)

            PortfolioView()
                .tabItem { Label("Portfolio", systemImage: "chart.line.uptrend.xyaxis") }
                .tag(Tab.portfolio)

            SettingsView()
                .tabItem { Label("Settings", systemImage: "gearshape.fill") }
                .tag(Tab.settings)
        }
        .tint(Color.signalGreen)
        .onAppear {
            Task { await appState.refreshAll() }
            appState.startPolling()
        }
        .onDisappear { appState.stopPolling() }
        .onReceive(NotificationCenter.default.publisher(for: .openSignal)) { note in
            if let id = note.userInfo?["signal_id"] as? String {
                deepLinkedSignalId = id
                selectedTab = .signals
            }
        }
    }
}
