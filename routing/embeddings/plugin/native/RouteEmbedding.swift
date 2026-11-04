import Foundation
import NaturalLanguage
import CryptoKit

struct Request: Decodable { let texts: [String]; let engine: String?; let prepare: Bool? }
struct Prototype: Codable { let language: String; let tier: String; let vector: [Double] }
struct PrototypeCache: Codable { let hash: String; let revisions: [String: Int]; let entries: [Prototype] }
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
    let catalogURL = URL(fileURLWithPath: CommandLine.arguments[1])
    let catalogData = try Data(contentsOf: catalogURL)
    let examples = try JSONDecoder().decode([Example].self, from: catalogData)
    let digest = SHA256.hash(data: catalogData).map { String(format: "%02x", $0) }.joined()
    let cacheURL = catalogURL.deletingLastPathComponent().appendingPathComponent("sentence-prototypes.plist")
    let saved = (try? Data(contentsOf: cacheURL)).flatMap { try? PropertyListDecoder().decode(PrototypeCache.self, from: $0) }

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
        let started = ProcessInfo.processInfo.systemUptime
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
            let cached = saved?.entries.filter { $0.language == language } ?? []
            let expected = examples.filter { $0.language == language }
            if engine == "sentence", let model = models[language], saved?.hash == digest,
               saved?.revisions[language] == model.revision, cached.count == expected.count,
               zip(cached, expected).allSatisfy({ $0.tier == $1.tier && $0.vector.count == model.dimension && $0.vector.allSatisfy({ $0.isFinite }) }) {
                vectors[language] = cached.map { ($0.tier, $0.vector) }
            } else {
                vectors[language] = try expected.compactMap { sample in
                    try vector(sample.text, detected!).map { (sample.tier, $0) }
                }
            }
        }
        let prepared = ProcessInfo.processInfo.systemUptime
        guard let query = try vector(text, detected!), let anchors = vectors[language], !anchors.isEmpty else {
            output.append(["available": false, "reason": "embedding-unavailable"]); continue
        }
        let revision = engine == "contextual" ? contextual[language]!.revision : models[language]!.revision
        let queried = ProcessInfo.processInfo.systemUptime
        var scores: [String: [Double]] = [:]
        for (tier, vector) in anchors { scores[tier, default: []].append(cosine(query, vector)) }
        let ranked: [(String, Double)] = scores.map { (tier: String, values: [Double]) -> (String, Double) in
            let top = values.sorted(by: >).prefix(2)
            return (tier, top.reduce(0, +) / Double(top.count))
        }.sorted { $0.1 == $1.1 ? $0.0 < $1.0 : $0.1 > $1.1 }
        let best = ranked[0], margin = best.1 - ranked[1].1
        let accepted = best.1 >= 0.40 && margin >= 0.025
        let scored = ProcessInfo.processInfo.systemUptime
        output.append(["timings": ["preparationMs": (prepared-started)*1000, "queryMs": (queried-prepared)*1000, "scoringMs": (scored-queried)*1000, "helperMs": (scored-started)*1000], "available": true, "language": language, "revision": revision, "engine": engine,
                       "tier": accepted ? best.0 : "standard", "candidate": best.0,
                       "score": best.1, "margin": margin, "ambiguous": !accepted])
    }
    if request.prepare == true && engine == "sentence" && vectors["fr"]?.count == examples.filter({ $0.language == "fr" }).count && vectors["en"]?.count == examples.filter({ $0.language == "en" }).count {
        let entries = examples.compactMap { sample -> Prototype? in
            guard let vector = models[sample.language]?.vector(for: sample.text) else { return nil }
            return Prototype(language: sample.language, tier: sample.tier, vector: vector)
        }
        let encoder = PropertyListEncoder(); encoder.outputFormat = .binary
        try encoder.encode(PrototypeCache(hash: digest, revisions: models.mapValues { $0.revision }, entries: entries)).write(to: cacheURL, options: .atomic)
    }
    FileHandle.standardOutput.write(try JSONSerialization.data(withJSONObject: output, options: [.sortedKeys]))
} catch { exit(1) }
