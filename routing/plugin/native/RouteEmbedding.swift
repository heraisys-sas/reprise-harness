import Foundation
import NaturalLanguage

struct Request: Decodable { let texts: [String]; let engine: String? }
struct Example: Decodable { let language: String; let tier: String; let text: String }
func cosine(_ a: [Double], _ b: [Double]) -> Double {
    guard a.count == b.count else { return -1 }
    let dot = zip(a, b).reduce(0) { $0 + $1.0 * $1.1 }
    let norm = sqrt(a.reduce(0) { $0 + $1 * $1 } * b.reduce(0) { $0 + $1 * $1 })
    return norm > 0 ? dot / norm : -1
}
do {
    let data = FileHandle.standardInput.readDataToEndOfFile()
    guard data.count <= 65536, CommandLine.arguments.count == 2 else { exit(2) }
    let request = try JSONDecoder().decode(Request.self, from: data)
    guard request.texts.count <= 64 else { exit(2) }
    let examples = try JSONDecoder().decode([Example].self, from: Data(contentsOf: URL(fileURLWithPath: CommandLine.arguments[1])))
    var vectors: [String: [(String, [Double])]] = [:]
    var models: [String: NLEmbedding] = [:]
    var contextual: [String: NLContextualEmbedding] = [:]
    let engine = request.engine == "contextual" ? "contextual" : "sentence"
    func vector(_ text: String, _ language: NLLanguage) throws -> [Double]? {
        if engine == "sentence" { return models[language.rawValue]?.vector(for: text) }
        guard let model = contextual[language.rawValue] else { return nil }
        let result = try model.embeddingResult(for: text, language: language)
        var sum = [Double](repeating: 0, count: model.dimension), count = 0
        result.enumerateTokenVectors(in: text.startIndex..<text.endIndex) { values, _ in
            for index in sum.indices { sum[index] += values[index] }
            count += 1
            return true
        }
        return count > 0 ? sum.map { $0 / Double(count) } : nil
    }
    var output: [[String: Any]] = []
    for original in request.texts {
        let text = String(original.prefix(4000))
        let detected = NLLanguageRecognizer.dominantLanguage(for: text)
        guard detected == .french || detected == .english else {
            output.append(["available": false, "reason": "unsupported-language"]); continue
        }
        let language = detected!.rawValue
        if vectors[language] == nil {
            if engine == "contextual", let model = NLContextualEmbedding(language: detected!), model.hasAvailableAssets {
                try model.load()
                contextual[language] = model
            } else if engine == "sentence", let model = NLEmbedding.sentenceEmbedding(for: detected!) {
                models[language] = model
            }
            vectors[language] = try examples.filter { $0.language == language }.compactMap { sample in
                try vector(sample.text, detected!).map { (sample.tier, $0) }
            }
        }
        guard let query = try vector(text, detected!), let anchors = vectors[language], !anchors.isEmpty else {
            output.append(["available": false, "reason": "embedding-unavailable"]); continue
        }
        let revision = engine == "contextual" ? contextual[language]!.revision : models[language]!.revision
        var scores: [String: [Double]] = [:]
        for (tier, vector) in anchors { scores[tier, default: []].append(cosine(query, vector)) }
        let ranked: [(String, Double)] = scores.map { (tier: String, values: [Double]) -> (String, Double) in
            let top = values.sorted(by: >).prefix(2)
            return (tier, top.reduce(0, +) / Double(top.count))
        }.sorted { $0.1 == $1.1 ? $0.0 < $1.0 : $0.1 > $1.1 }
        let best = ranked[0], margin = best.1 - ranked[1].1
        let accepted = best.1 >= 0.40 && margin >= 0.025
        output.append(["available": true, "language": language, "revision": revision, "engine": engine,
                       "tier": accepted ? best.0 : "standard", "candidate": best.0,
                       "score": best.1, "margin": margin, "ambiguous": !accepted])
    }
    FileHandle.standardOutput.write(try JSONSerialization.data(withJSONObject: output, options: [.sortedKeys]))
} catch { exit(1) }
