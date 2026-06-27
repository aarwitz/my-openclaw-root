import SwiftUI

struct SettingsView: View {
    @EnvironmentObject private var appState: AppState
    @State private var serverURL: String = UserDefaults.standard.string(forKey: "api_base_url") ?? "http://localhost:8765"
    @State private var apiKey: String = UserDefaults.standard.string(forKey: "api_key") ?? ""
    @State private var showUnlinkConfirm = false
    @State private var showConnectSheet = false
    @State private var notificationsEnabled = false

    var body: some View {
        NavigationStack {
            ZStack {
                Color.background.ignoresSafeArea()
                List {
                    robinhoodSection
                    notificationSection
                    serverSection
                    aboutSection
                }
                .scrollContentBackground(.hidden)
                .listStyle(.insetGrouped)
            }
            .navigationTitle("Settings")
            .navigationBarTitleDisplayMode(.large)
            .sheet(isPresented: $showConnectSheet) {
                ConnectRobinhoodView {
                    showConnectSheet = false
                    Task { await appState.refreshAccount() }
                }
                .environmentObject(appState)
            }
            .confirmationDialog(
                "Unlink Robinhood?",
                isPresented: $showUnlinkConfirm,
                titleVisibility: .visible
            ) {
                Button("Unlink", role: .destructive) { unlinkRobinhood() }
                Button("Cancel", role: .cancel) {}
            } message: {
                Text("Your session token will be removed from the server.")
            }
            .task { await checkNotificationStatus() }
        }
    }

    // MARK: - Sections

    private var robinhoodSection: some View {
        Section {
            if appState.robinhoodLinked {
                HStack {
                    Image(systemName: "checkmark.circle.fill")
                        .foregroundStyle(.signalGreen)
                    VStack(alignment: .leading, spacing: 2) {
                        Text("Account linked")
                            .font(.subheadline.weight(.semibold))
                            .foregroundStyle(.textPrimary)
                        if let username = KeychainService.load(key: "rh_username") {
                            Text(username)
                                .font(.caption)
                                .foregroundStyle(.textSecondary)
                        }
                    }
                    Spacer()
                    Button("Unlink") { showUnlinkConfirm = true }
                        .font(.subheadline)
                        .foregroundStyle(.signalRed)
                }
            } else {
                Button(action: { showConnectSheet = true }) {
                    HStack {
                        Image(systemName: "link.circle.fill")
                            .foregroundStyle(.signalGreen)
                        Text("Connect Robinhood")
                            .foregroundStyle(.textPrimary)
                        Spacer()
                        Image(systemName: "chevron.right")
                            .font(.caption)
                            .foregroundStyle(.textTertiary)
                    }
                }
            }
        } header: { SectionHeader("ROBINHOOD") }
        .listRowBackground(Color.cardSurface)
    }

    private var notificationSection: some View {
        Section {
            HStack {
                Image(systemName: "bell.fill")
                    .foregroundStyle(.accent)
                Text("Signal alerts")
                    .foregroundStyle(.textPrimary)
                Spacer()
                Toggle("", isOn: $notificationsEnabled)
                    .tint(.signalGreen)
                    .onChange(of: notificationsEnabled) { enabled in
                        if enabled { requestNotifications() }
                    }
            }
            Text("You'll get a push notification the moment a new signal clears all risk gates.")
                .font(.caption)
                .foregroundStyle(.textSecondary)
        } header: { SectionHeader("NOTIFICATIONS") }
        .listRowBackground(Color.cardSurface)
    }

    private var serverSection: some View {
        Section {
            VStack(alignment: .leading, spacing: 6) {
                Text("Server URL")
                    .font(.caption.weight(.semibold))
                    .foregroundStyle(.textTertiary)
                TextField("http://your-tailscale-ip:8765", text: $serverURL)
                    .font(.system(.subheadline, design: .monospaced))
                    .foregroundStyle(.textPrimary)
                    .autocorrectionDisabled()
                    .textInputAutocapitalization(.never)
                    .onSubmit { saveServerURL() }
            }
            VStack(alignment: .leading, spacing: 6) {
                Text("API Key")
                    .font(.caption.weight(.semibold))
                    .foregroundStyle(.textTertiary)
                SecureField("Optional", text: $apiKey)
                    .font(.system(.subheadline, design: .monospaced))
                    .foregroundStyle(.textPrimary)
                    .onSubmit { saveAPIKey() }
            }
            Button("Save Connection") {
                saveServerURL()
                saveAPIKey()
            }
            .foregroundStyle(.signalGreen)
        } header: { SectionHeader("CONNECTION") }
        .listRowBackground(Color.cardSurface)
    }

    private var aboutSection: some View {
        Section {
            LabeledContent("Version") {
                Text(Bundle.main.infoDictionary?["CFBundleShortVersionString"] as? String ?? "—")
                    .foregroundStyle(.textSecondary)
            }
            LabeledContent("Engine") {
                Text("AutoTrade v4 · 9-agent system")
                    .foregroundStyle(.textSecondary)
            }
            Button("Reset Onboarding") {
                appState.isOnboarded = false
            }
            .foregroundStyle(.signalRed)
        } header: { SectionHeader("ABOUT") }
        .listRowBackground(Color.cardSurface)
    }

    // MARK: - Actions

    private func saveServerURL() {
        UserDefaults.standard.set(serverURL, forKey: "api_base_url")
    }

    private func saveAPIKey() {
        UserDefaults.standard.set(apiKey, forKey: "api_key")
    }

    private func unlinkRobinhood() {
        Task {
            try? await APIClient.shared.unlinkRobinhood()
            await MainActor.run {
                KeychainService.delete(key: "rh_username")
                appState.robinhoodLinked = false
                appState.portfolio = nil
            }
        }
    }

    private func requestNotifications() {
        Task {
            let granted = await NotificationService.shared.requestPermission()
            if granted { NotificationService.shared.registerForRemotePush() }
            await MainActor.run { notificationsEnabled = granted }
        }
    }

    private func checkNotificationStatus() async {
        let settings = await UNUserNotificationCenter.current().notificationSettings()
        await MainActor.run {
            notificationsEnabled = settings.authorizationStatus == .authorized
        }
    }
}

private struct SectionHeader: View {
    let title: String
    init(_ title: String) { self.title = title }
    var body: some View {
        Text(title)
            .font(.system(size: 11, weight: .semibold))
            .foregroundStyle(.textTertiary)
    }
}
