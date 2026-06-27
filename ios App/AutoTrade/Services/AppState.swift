import Foundation
import Combine

@MainActor
final class AppState: ObservableObject {
    static let shared = AppState()

    @Published var signals: [Signal] = []
    @Published var portfolio: Portfolio?
    @Published var account: RobinhoodAccount?
    @Published var performance: PerformanceSummary?

    @Published var isLoadingSignals = false
    @Published var isLoadingPortfolio = false
    @Published var signalError: String?
    @Published var portfolioError: String?

    @Published var isOnboarded: Bool {
        didSet { UserDefaults.standard.set(isOnboarded, forKey: "is_onboarded") }
    }
    @Published var robinhoodLinked: Bool {
        didSet { UserDefaults.standard.set(robinhoodLinked, forKey: "robinhood_linked") }
    }

    // Signal polling interval (seconds)
    private let pollInterval: TimeInterval = 30
    private var pollTask: Task<Void, Never>?

    init() {
        isOnboarded = UserDefaults.standard.bool(forKey: "is_onboarded")
        robinhoodLinked = UserDefaults.standard.bool(forKey: "robinhood_linked")
    }

    func startPolling() {
        pollTask?.cancel()
        pollTask = Task { [weak self] in
            while !Task.isCancelled {
                await self?.refreshAll()
                try? await Task.sleep(nanoseconds: UInt64(30 * 1_000_000_000))
            }
        }
    }

    func stopPolling() {
        pollTask?.cancel()
    }

    func refreshAll() async {
        await withTaskGroup(of: Void.self) { group in
            group.addTask { await self.refreshSignals() }
            if self.robinhoodLinked {
                group.addTask { await self.refreshPortfolio() }
            }
        }
    }

    func refreshSignals() async {
        isLoadingSignals = true
        signalError = nil
        do {
            signals = try await APIClient.shared.fetchSignals()
        } catch {
            signalError = error.localizedDescription
        }
        isLoadingSignals = false
    }

    func refreshPortfolio() async {
        isLoadingPortfolio = true
        portfolioError = nil
        do {
            async let p = APIClient.shared.fetchPortfolio()
            async let perf = APIClient.shared.fetchPerformance()
            portfolio = try await p
            performance = try await perf
        } catch {
            portfolioError = error.localizedDescription
        }
        isLoadingPortfolio = false
    }

    func refreshAccount() async {
        do {
            account = try await APIClient.shared.fetchAccountStatus()
            robinhoodLinked = account?.isLinked ?? false
        } catch {
            robinhoodLinked = false
        }
    }

    // New signals since last check — used for notification badge
    var newSignalCount: Int {
        signals.filter { $0.state.isActionable }.count
    }

    var activeSignals: [Signal] {
        signals.filter { $0.state == .approved || $0.state == .submitted }
    }

    var recentFills: [Signal] {
        signals.filter { $0.state == .filled }
            .sorted { ($0.filledAt ?? .distantPast) > ($1.filledAt ?? .distantPast) }
            .prefix(10)
            .map { $0 }
    }
}
