import Foundation
import FoundationModels

@available(macOS 26.0, *)
func trySize(_ approxTokens: Int) async {
    // ~4 chars per token for English
    let words = approxTokens
    let text = Array(repeating: "alpha beta gamma delta epsilon", count: max(1, words / 5)).joined(separator: " ")
    let session = LanguageModelSession(instructions: "Answer with a single word: sum or product. Nothing else.")
    do {
        let r = try await session.respond(to: "Ignore the following padding text completely. Padding: \(text)\nQuestion: what is 2+2? Answer sum or product of nothing; just reply with the word: correct")
        print("\(approxTokens) tokens (~\(text.count) chars): OK — \(r.content.prefix(40))")
    } catch {
        print("\(approxTokens) tokens (~\(text.count) chars): ERROR — \(error)")
    }
}

@available(macOS 26.0, *)
func run() async {
    for t in [1000, 3000, 4000, 6000, 8000, 12000, 16000] {
        await trySize(t)
    }
}

let sem = DispatchSemaphore(value: 0)
if #available(macOS 26.0, *) {
    Task { await run(); sem.signal() }
} else {
    print("requires macOS 26+"); sem.signal()
}
sem.wait()
