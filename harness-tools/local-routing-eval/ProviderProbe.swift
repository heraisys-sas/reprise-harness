import Foundation
import FoundationModels

let system = SystemLanguageModel.default
let cloud = PrivateCloudComputeLanguageModel()
let report: [String: Any] = [
    "system_available": system.isAvailable,
    "system_availability": String(describing: system.availability),
    "pcc_available": cloud.isAvailable,
    "pcc_availability": String(describing: cloud.availability),
    "generation_requested": false,
    "note": "SDK availability only; no model downloaded or transcript sent."
]
let output = try JSONSerialization.data(withJSONObject: report, options: [.sortedKeys])
FileHandle.standardOutput.write(output)
