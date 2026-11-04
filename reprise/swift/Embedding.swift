import Foundation
import NaturalLanguage

let input = FileHandle.standardInput.readDataToEndOfFile()
do {
    let values = try JSONSerialization.jsonObject(with: input) as? [String] ?? []
    var output: [[String: Any]] = []
    for text in values.prefix(100) {
        let language = NLLanguageRecognizer.dominantLanguage(for: text) ?? .english
        let selected: NLLanguage = language == .french ? .french : .english
        if let model = NLEmbedding.sentenceEmbedding(for: selected), let vector = model.vector(for: text) {
            output.append(["language": selected.rawValue, "revision": model.revision, "vector": vector])
        } else {
            output.append(["language": selected.rawValue, "vector": NSNull()])
        }
    }
    FileHandle.standardOutput.write(try JSONSerialization.data(withJSONObject: output))
} catch {
    FileHandle.standardOutput.write(Data("[]".utf8))
    exit(1)
}
