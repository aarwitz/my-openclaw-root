import Foundation

struct RobinhoodAccount: Codable {
    let username: String
    let accountNumber: String
    let buyingPower: Double
    let portfolioValue: Double
    let isLinked: Bool
    let linkedAt: Date?
}

// Stored securely in Keychain — never persisted to disk
struct RobinhoodCredential {
    let username: String
    let password: String
    let mfaCode: String?   // TOTP code if MFA is enabled
}

// Response from POST /api/robinhood/link
struct LinkResult: Codable {
    let success: Bool
    let requiresMFA: Bool
    let accountNumber: String?
    let error: String?
}
