import Foundation
import FoundationModels

@available(macOS 26.0, *)
@Generable
struct RouteDecision {
    @Guide(description: "Identifier of the model to route the task to")
    var best_model: String
    @Guide(description: "Confidence between 0.0 and 1.0")
    var confidence: Double
}

@available(macOS 26.0, *)
func run() async throws {
    let availability = SystemLanguageModel.default.availability
    print("availability: \(availability)")
    guard case .available = availability else {
        print("UNAVAILABLE — smoke test stops here")
        return
    }

    let session = LanguageModelSession(instructions: "You are a routing classifier. Answer only with the requested structure. Keep identifiers in English.")

    // Test 1: simple completion
    let t0 = Date()
    let text = try await session.respond(to: "Translate to English: « Le routage par classification sémantique reste optionnel. »")
    print("test1_translation (\(Int(-t0.timeIntervalSinceNow)*1000)ms): \(text.content)")

    // Test 2: guided generation (the decision shape that matters for arm A)
    let t1 = Date()
    let decision = try await session.respond(to: "Task: fix a typo in a UI label and update the corresponding unit test.", generating: RouteDecision.self)
    let ms = Int(-t1.timeIntervalSinceNow * 1000)
    print("test2_guided (\(ms)ms): best_model=\(decision.content.best_model) confidence=\(decision.content.confidence)")

    // Test 3: harder routing question — does it escalate?
    let t2 = Date()
    let decision2 = try await session.respond(to: "Task: redesign the delegation state machine to avoid property races between two concurrent sessions; requires distributed-systems reasoning.", generating: RouteDecision.self)
    let ms2 = Int(-t2.timeIntervalSinceNow * 1000)
    print("test3_guided (\(ms2)ms): best_model=\(decision2.content.best_model) confidence=\(decision2.content.confidence)")
}

let sem = DispatchSemaphore(value: 0)
if #available(macOS 26.0, *) {
    let mirror = Mirror(reflecting: Task {
        do { try await run() } catch { print("ERROR: \(error)") }
        sem.signal()
    })
    _ = mirror
} else {
    print("requires macOS 26+")
    sem.signal()
}
sem.wait()
