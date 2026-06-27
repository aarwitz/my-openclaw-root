import SwiftUI

@main
struct AutoTradeApp: App {
    @UIApplicationDelegateAdaptor(AppDelegate.self) var delegate
    @StateObject private var appState = AppState.shared

    var body: some Scene {
        WindowGroup {
            Group {
                if appState.isOnboarded {
                    ContentView()
                } else {
                    OnboardingView()
                }
            }
            .environmentObject(appState)
            .preferredColorScheme(.dark)
            .onReceive(NotificationCenter.default.publisher(for: .openSignal)) { _ in
                // Deep-link handling wired through AppState
            }
        }
    }
}

final class AppDelegate: NSObject, UIApplicationDelegate {
    func application(
        _ application: UIApplication,
        didFinishLaunchingWithOptions launchOptions: [UIApplication.LaunchOptionsKey: Any]? = nil
    ) -> Bool {
        NotificationService.shared.registerForRemotePush()
        return true
    }

    func application(_ application: UIApplication, didRegisterForRemoteNotificationsWithDeviceToken deviceToken: Data) {
        NotificationService.shared.handleDeviceToken(deviceToken)
    }

    func application(_ application: UIApplication, didFailToRegisterForRemoteNotificationsWithError error: Error) {
        print("Push registration failed: \(error)")
    }
}
