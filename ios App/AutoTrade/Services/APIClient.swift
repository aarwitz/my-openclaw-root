import Foundation
import Combine

final class APIClient {
    static let shared = APIClient()

    // Reads from UserDefaults — user sets this in Settings
    var baseURL: URL {
        let stored = UserDefaults.standard.string(forKey: "api_base_url") ?? "http://localhost:8765"
        return URL(string: stored)!
    }

    private let session: URLSession
    private let decoder: JSONDecoder

    private init() {
        let config = URLSessionConfiguration.default
        config.timeoutIntervalForRequest = 15
        session = URLSession(configuration: config)
        decoder = JSONDecoder()
        decoder.dateDecodingStrategy = .iso8601
        decoder.keyDecodingStrategy = .convertFromSnakeCase
    }

    // MARK: - Signals

    func fetchSignals(states: [Signal.State] = [.approved, .submitted, .filled]) async throws -> [Signal] {
        let stateParam = states.map(\.rawValue).joined(separator: ",")
        var comps = URLComponents(url: baseURL.appendingPathComponent("api/signals"), resolvingAgainstBaseURL: false)!
        comps.queryItems = [URLQueryItem(name: "state", value: stateParam), URLQueryItem(name: "limit", value: "50")]
        return try await get(comps.url!)
    }

    func fetchSignal(id: String) async throws -> Signal {
        try await get(baseURL.appendingPathComponent("api/signals/\(id)"))
    }

    func executeSignal(id: String, quantity: Double) async throws -> ExecuteResult {
        let body = ["quantity": quantity]
        return try await post(baseURL.appendingPathComponent("api/signals/\(id)/execute"), body: body)
    }

    // MARK: - Portfolio

    func fetchPortfolio() async throws -> Portfolio {
        try await get(baseURL.appendingPathComponent("api/portfolio"))
    }

    func fetchPerformance(days: Int = 30) async throws -> PerformanceSummary {
        var comps = URLComponents(url: baseURL.appendingPathComponent("api/performance"), resolvingAgainstBaseURL: false)!
        comps.queryItems = [URLQueryItem(name: "days", value: "\(days)")]
        return try await get(comps.url!)
    }

    // MARK: - Robinhood auth

    func linkRobinhood(username: String, password: String, mfaCode: String? = nil) async throws -> LinkResult {
        var body: [String: String] = ["username": username, "password": password]
        if let mfa = mfaCode { body["mfa_code"] = mfa }
        return try await post(baseURL.appendingPathComponent("api/robinhood/link"), body: body)
    }

    func fetchAccountStatus() async throws -> RobinhoodAccount {
        try await get(baseURL.appendingPathComponent("api/robinhood/status"))
    }

    func unlinkRobinhood() async throws {
        let _: EmptyResponse = try await post(baseURL.appendingPathComponent("api/robinhood/unlink"), body: EmptyBody())
    }

    // MARK: - Push notifications

    func registerPushToken(_ token: String) async throws {
        let body = ["device_token": token, "platform": "ios"]
        let _: EmptyResponse = try await post(baseURL.appendingPathComponent("api/push/register"), body: body)
    }

    // MARK: - Helpers

    private func get<T: Decodable>(_ url: URL) async throws -> T {
        var req = URLRequest(url: url)
        req.setValue(apiKey, forHTTPHeaderField: "X-AutoTrade-Key")
        let (data, response) = try await session.data(for: req)
        try validate(response)
        return try decoder.decode(T.self, from: data)
    }

    private func post<B: Encodable, T: Decodable>(_ url: URL, body: B) async throws -> T {
        var req = URLRequest(url: url)
        req.httpMethod = "POST"
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.setValue(apiKey, forHTTPHeaderField: "X-AutoTrade-Key")
        req.httpBody = try JSONEncoder().encode(body)
        let (data, response) = try await session.data(for: req)
        try validate(response)
        return try decoder.decode(T.self, from: data)
    }

    private func validate(_ response: URLResponse) throws {
        guard let http = response as? HTTPURLResponse else { throw APIError.invalidResponse }
        guard (200..<300).contains(http.statusCode) else { throw APIError.httpError(http.statusCode) }
    }

    private var apiKey: String {
        UserDefaults.standard.string(forKey: "api_key") ?? ""
    }
}

struct ExecuteResult: Codable {
    let success: Bool
    let orderId: String?
    let error: String?
}

enum APIError: LocalizedError {
    case invalidResponse
    case httpError(Int)
    var errorDescription: String? {
        switch self {
        case .invalidResponse: return "Invalid server response"
        case .httpError(let code): return "Server error \(code)"
        }
    }
}

private struct EmptyBody: Encodable {}
private struct EmptyResponse: Decodable {}
