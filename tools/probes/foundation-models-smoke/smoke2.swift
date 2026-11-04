import Foundation
import FoundationModels

@available(macOS 26.0, *)
@Generable
struct RouteChoice {
    @Guide(description: "One of: flash, pro, codex, decline")
    var best_model: String
    @Guide(description: "Confidence between 0.0 and 1.0")
    var confidence: Double
    @Guide(description: "One-sentence justification in English")
    var reason: String
}

@available(macOS 26.0, *)
func run() async throws {
    let session = LanguageModelSession(instructions: """
    You are a routing classifier for an agent harness. Choose the best target model from this fixed registry:
    - flash: short, well-scoped tasks (label edits, targeted fixes, small tests)
    - pro: algorithmic difficulty, architectural redesign, critical review
    - codex: external expert delegation when a superior model is required
    - decline: do not route (trivial or out of scope)
    Respond only with the required structure.
    """)

    let cases: [(String, String)] = [
        ("D1", "Fix a typo in a UI label and update the corresponding unit test."),
        ("D1", "Add a badge to the dashboard header with its existing unit tests."),
        ("D2", "Redesign the delegation state machine to avoid property races between two concurrent sessions; distributed-systems reasoning required."),
        ("D2", "Reconcile a SQLite state machine with non-idempotent network retries; the naive fix looped."),
        ("D5", "Summarize this conversation for the user."),
    ]
    for (id, task) in cases {
        let t0 = Date()
        let r = try await session.respond(to: task, generating: RouteChoice.self)
        let ms = Int(-t0.timeIntervalSinceNow * 1000)
        print("\(id) (\(ms)ms): \(r.content.best_model) conf=\(r.content.confidence) — \(r.content.reason)")
    }
}

let sem = DispatchSemaphore(value: 0)
if #available(macOS 26.0, *) {
    Task {
        do { try await run() } catch { print("ERROR: \(error)") }
        sem.signal()
    }
} else {
    print("requires macOS 26+")
    sem.signal()
}
sem.wait()
