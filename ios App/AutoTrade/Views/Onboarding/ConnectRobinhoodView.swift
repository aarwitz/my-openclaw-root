import SwiftUI

struct ConnectRobinhoodView: View {
    var onComplete: (() -> Void)? = nil

    @EnvironmentObject private var appState: AppState
    @State private var username = ""
    @State private var password = ""
    @State private var mfaCode = ""
    @State private var showMFA = false
    @State private var isLoading = false
    @State private var error: String?
    @State private var showSkip = false

    var body: some View {
        ScrollView {
            VStack(spacing: 28) {
                Spacer().frame(height: 20)

                // Header
                VStack(spacing: 12) {
                    Image(systemName: "link.circle.fill")
                        .font(.system(size: 56))
                        .foregroundStyle(.signalGreen)
                    Text("Connect Robinhood")
                        .font(.system(.title, design: .rounded, weight: .bold))
                        .foregroundStyle(.textPrimary)
                    Text("Your credentials are sent securely to your personal AutoTrade server. They are never stored by Lidi Solutions.")
                        .font(.subheadline)
                        .foregroundStyle(.textSecondary)
                        .multilineTextAlignment(.center)
                        .padding(.horizontal, 8)
                }

                // Form
                VStack(spacing: 14) {
                    StyledTextField(label: "Robinhood email", text: $username, keyboard: .emailAddress, isSecure: false)
                    StyledTextField(label: "Password", text: $password, keyboard: .default, isSecure: true)

                    if showMFA {
                        StyledTextField(label: "2FA code (6 digits)", text: $mfaCode, keyboard: .numberPad, isSecure: false)
                    }
                }
                .padding(.horizontal, 24)

                // Error
                if let error {
                    Text(error)
                        .font(.subheadline)
                        .foregroundStyle(.signalRed)
                        .padding(.horizontal, 24)
                        .multilineTextAlignment(.center)
                }

                // Connect button
                Button(action: connect) {
                    Group {
                        if isLoading {
                            ProgressView().tint(.black)
                        } else {
                            Text("Connect Account")
                                .font(.headline)
                        }
                    }
                    .frame(maxWidth: .infinity)
                    .padding(.vertical, 16)
                    .background(canSubmit ? Color.signalGreen : Color.textTertiary)
                    .foregroundStyle(canSubmit ? .black : .textSecondary)
                    .clipShape(RoundedRectangle(cornerRadius: 14, style: .continuous))
                }
                .disabled(!canSubmit || isLoading)
                .padding(.horizontal, 24)
                .animation(.easeInOut(duration: 0.2), value: canSubmit)

                // Skip
                Button("Skip for now — browse signals only") {
                    onComplete?()
                }
                .font(.subheadline)
                .foregroundStyle(.textTertiary)
                .padding(.bottom, 80)
            }
        }
        .background(Color.background.ignoresSafeArea())
    }

    private var canSubmit: Bool {
        !username.isEmpty && !password.isEmpty && (!showMFA || !mfaCode.isEmpty)
    }

    private func connect() {
        error = nil
        isLoading = true
        UIApplication.shared.sendAction(#selector(UIResponder.resignFirstResponder), to: nil, from: nil, for: nil)

        Task {
            do {
                let result = try await APIClient.shared.linkRobinhood(
                    username: username,
                    password: password,
                    mfaCode: showMFA ? mfaCode : nil
                )
                await MainActor.run {
                    isLoading = false
                    if result.requiresMFA && !showMFA {
                        showMFA = true
                        error = "Enter the 6-digit code from your authenticator app."
                    } else if result.success {
                        UINotificationFeedbackGenerator().notificationOccurred(.success)
                        KeychainService.save(key: "rh_username", value: username)
                        onComplete?()
                    } else {
                        error = result.error ?? "Link failed. Check your credentials."
                        UINotificationFeedbackGenerator().notificationOccurred(.error)
                    }
                }
            } catch {
                await MainActor.run {
                    isLoading = false
                    self.error = error.localizedDescription
                }
            }
        }
    }
}

private struct StyledTextField: View {
    let label: String
    @Binding var text: String
    let keyboard: UIKeyboardType
    let isSecure: Bool

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(label)
                .font(.caption.weight(.semibold))
                .foregroundStyle(.textTertiary)
            Group {
                if isSecure {
                    SecureField("", text: $text)
                } else {
                    TextField("", text: $text)
                        .keyboardType(keyboard)
                        .autocorrectionDisabled()
                        .textInputAutocapitalization(.never)
                }
            }
            .padding(.horizontal, 14)
            .padding(.vertical, 12)
            .background(Color.cardSurface)
            .foregroundStyle(.textPrimary)
            .clipShape(RoundedRectangle(cornerRadius: 10, style: .continuous))
            .overlay(
                RoundedRectangle(cornerRadius: 10, style: .continuous)
                    .stroke(Color.cardBorder, lineWidth: 0.5)
            )
        }
    }
}
