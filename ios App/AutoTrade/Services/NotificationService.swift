import Foundation
import UserNotifications
import UIKit

final class NotificationService: NSObject, UNUserNotificationCenterDelegate {
    static let shared = NotificationService()

    override init() {
        super.init()
        UNUserNotificationCenter.current().delegate = self
    }

    func requestPermission() async -> Bool {
        let center = UNUserNotificationCenter.current()
        let options: UNAuthorizationOptions = [.alert, .sound, .badge]
        return (try? await center.requestAuthorization(options: options)) ?? false
    }

    func registerForRemotePush() {
        DispatchQueue.main.async {
            UIApplication.shared.registerForRemoteNotifications()
        }
    }

    func handleDeviceToken(_ tokenData: Data) {
        let token = tokenData.map { String(format: "%02.2hhx", $0) }.joined()
        KeychainService.save(key: "push_device_token", value: token)
        Task {
            try? await APIClient.shared.registerPushToken(token)
        }
    }

    // Foreground notification display
    func userNotificationCenter(
        _ center: UNUserNotificationCenter,
        willPresent notification: UNNotification,
        withCompletionHandler completionHandler: @escaping (UNNotificationPresentationOptions) -> Void
    ) {
        completionHandler([.banner, .sound, .badge])
    }

    // Tap on notification → navigate to signal
    func userNotificationCenter(
        _ center: UNUserNotificationCenter,
        didReceive response: UNNotificationResponse,
        withCompletionHandler completionHandler: @escaping () -> Void
    ) {
        let userInfo = response.notification.request.content.userInfo
        if let signalId = userInfo["signal_id"] as? String {
            NotificationCenter.default.post(
                name: .openSignal,
                object: nil,
                userInfo: ["signal_id": signalId]
            )
        }
        completionHandler()
    }
}

extension Notification.Name {
    static let openSignal = Notification.Name("OpenSignalNotification")
}
