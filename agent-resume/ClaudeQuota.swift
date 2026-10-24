import AppKit
import ApplicationServices
import CryptoKit
import Foundation

// Narrow AX adapter. It never focuses a window or writes into the composer.
// Observation is not proof of submission; a successful press is only dispatch evidence.
struct ProbeFailure: Error { let code: String }
func fail(_ code: String) throws -> Never { throw ProbeFailure(code: code) }
func attr(_ element: AXUIElement, _ name: String) -> CFTypeRef? {
    var value: CFTypeRef?
    return AXUIElementCopyAttributeValue(element, name as CFString, &value) == .success ? value : nil
}
func text(_ element: AXUIElement, _ name: String) -> String {
    guard let value = attr(element, name) else { return "" }
    if let string = value as? String { return string }
    if let url = value as? URL { return url.absoluteString }
    return ""
}
func labels(_ element: AXUIElement) -> [String] {
    [kAXTitleAttribute, kAXDescriptionAttribute, kAXValueAttribute].map { text(element, $0) }
}
func children(_ element: AXUIElement) -> [AXUIElement] {
    (attr(element, kAXChildrenAttribute) as? [AXUIElement]) ?? []
}
func hash(_ string: String) -> String {
    SHA256.hash(data: Data(string.utf8)).map { String(format: "%02x", $0) }.joined()
}
func canonicalChat(_ value: String) -> String? {
    guard let url = URL(string: value), url.scheme == "https", url.host == "claude.ai",
          url.user == nil, url.password == nil else { return nil }
    let segments = url.path.split(separator: "/")
    guard segments.count == 2, segments[0] == "chat", let uuid = UUID(uuidString: String(segments[1])) else { return nil }
    return uuid.uuidString.lowercased()
}
struct Snapshot {
    let button: AXUIElement
    let chat: String
    let ordinal: Int
    let pause: String
    let messageID: String
    let enabled: Bool
    let composerEmpty: Bool
    let busy: Bool
    var fingerprint: String { hash(["claude-quota-ax-v1", chat, String(ordinal), messageID, pause].joined(separator: "\n")) }
    var json: [String: Any] {
        ["status": "observed", "chat_id": chat, "last_message_ordinal": ordinal,
         "last_message_id": hash(messageID), "pause_text": pause, "continue_enabled": enabled,
         "composer_empty": composerEmpty, "busy": busy, "timezone": TimeZone.current.identifier,
         "evidence_sha256": fingerprint, "source": "claude-desktop-accessibility",
         "completion_confirmed": false]
    }
}
func observe(_ expectedChat: String?) throws -> Snapshot {
    guard AXIsProcessTrusted() else { try fail("accessibility_permission_required") }
    let apps = NSRunningApplication.runningApplications(withBundleIdentifier: "com.anthropic.claudefordesktop")
    guard apps.count == 1, let app = apps.first else { try fail("claude_process_ambiguous_or_absent") }
    let root = AXUIElementCreateApplication(app.processIdentifier)
    AXUIElementSetMessagingTimeout(root, 0.2)
    var budget = 5000
    func walk(_ element: AXUIElement, _ depth: Int, _ visit: (AXUIElement) throws -> Bool) throws {
        budget -= 1
        guard budget >= 0, depth <= 40 else { try fail("accessibility_tree_limit") }
        if try !visit(element) { return }
        for child in children(element) { try walk(child, depth + 1, visit) }
    }
    var areas: [(AXUIElement, String)] = []
    try walk(root, 0) { element in
        if text(element, kAXRoleAttribute) == "AXWebArea" {
            if let found = canonicalChat(text(element, kAXURLAttribute)), expectedChat == nil || found == expectedChat {
                areas.append((element, found)); return false
            }
        }
        return true
    }
    guard areas.count == 1, let (area, chat) = areas.first else { try fail("exact_chat_webarea_unavailable") }
    let regex = try NSRegularExpression(pattern: "^Message ([0-9]+) sur ([0-9]+)$")
    var messages: [(AXUIElement, Int)] = []
    var composers: [AXUIElement] = []
    var busy = false
    try walk(area, 0) { element in
        if text(element, kAXRoleAttribute) == kAXButtonRole && labels(element).contains(where: { ["Arrêter la réponse", "Stop response", "Stop generating", "Arrêter"].contains($0) }) { busy = true }
        if [kAXTextAreaRole, kAXTextFieldRole].contains(text(element, kAXRoleAttribute)) &&
            text(element, kAXDescriptionAttribute).trimmingCharacters(in: CharacterSet(charactersIn: ". ")) == "Écrivez votre invite à Claude" { composers.append(element) }
        for label in labels(element) {
            let range = NSRange(label.startIndex..<label.endIndex, in: label)
            guard let match = regex.firstMatch(in: label, range: range),
                  let a = Range(match.range(at: 1), in: label), let b = Range(match.range(at: 2), in: label),
                  let ordinal = Int(label[a]), let count = Int(label[b]), ordinal == count else { continue }
            messages.append((element, ordinal)); return false
        }
        return true
    }
    guard messages.count == 1, let (last, ordinal) = messages.first else { try fail("last_message_unavailable_or_ambiguous") }
    var pauses = Set<String>()
    var buttons: [AXUIElement] = []
    try walk(last, 0) { element in
        let values = labels(element)
        if text(element, kAXRoleAttribute) == kAXButtonRole && values.contains(where: { ["Arrêter la réponse", "Stop response", "Stop generating", "Arrêter"].contains($0) }) { busy = true }
        for value in values where value.hasPrefix("En pause") && value.contains("utilisation gratuite") && value.contains("disponible") {
            guard value.count <= 1200 else { try fail("pause_text_too_long") }
            pauses.insert(value)
        }
        if text(element, kAXRoleAttribute) == kAXButtonRole && values.contains("Continuer") { buttons.append(element) }
        return true
    }
    guard pauses.count == 1, let pause = pauses.first, buttons.count == 1, let button = buttons.first,
          let enabled = attr(button, kAXEnabledAttribute) as? Bool else { try fail("quota_pause_or_continue_unavailable") }
    guard composers.count == 1, let composer = composers.first,
          let composerValue = attr(composer, kAXValueAttribute) as? String else { try fail("composer_state_unavailable") }
    return Snapshot(button: button, chat: chat, ordinal: ordinal, pause: pause,
                    messageID: "\(ordinal):\(text(last, kAXIdentifierAttribute))", enabled: enabled,
                    composerEmpty: composerValue.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty, busy: busy)
}
func emit(_ value: [String: Any]) {
    let data = (try? JSONSerialization.data(withJSONObject: value, options: [.sortedKeys])) ?? Data("{}".utf8)
    FileHandle.standardOutput.write(data + Data("\n".utf8))
}
let args = Array(CommandLine.arguments.dropFirst())
func option(_ name: String) -> String? {
    guard let i = args.firstIndex(of: name), i + 1 < args.count else { return nil }
    return args[i + 1]
}
var dispatchAttempted = false
do {
    guard let command = args.first, ["observe", "continue"].contains(command) else { try fail("invalid_arguments") }
    let requested = option("--chat")
    if let requested, UUID(uuidString: requested) == nil { try fail("invalid_arguments") }
    let observed = try observe(requested.flatMap { UUID(uuidString: $0)?.uuidString.lowercased() })
    let chat = observed.chat
    if command == "observe" { emit(observed.json) }
    else {
        guard let evidence = option("--evidence"), evidence == observed.fingerprint else { try fail("evidence_changed") }
        guard observed.enabled else { try fail("continue_disabled") }
        guard !observed.busy else { try fail("chat_busy") }
        guard observed.composerEmpty else { try fail("composer_not_empty") }
        // Re-read the exact chat, final message and button immediately before dispatch.
        let current = try observe(chat)
        guard current.fingerprint == evidence, current.enabled, current.composerEmpty, !current.busy else { try fail("evidence_changed") }
        dispatchAttempted = true
        let result = AXUIElementPerformAction(current.button, kAXPressAction as CFString)
        guard result == .success else { try fail("continue_dispatch_failed") }
        emit(["status": "clicked", "chat_id": chat, "evidence_sha256": evidence,
              "dispatch_attempted": true, "completion_confirmed": false])
    }
} catch let error as ProbeFailure {
    emit(["status": dispatchAttempted ? "uncertain" : (args.first == "continue" ? "refused" : "unavailable"), "reason": error.code, "dispatch_attempted": dispatchAttempted])
    exit(1)
} catch {
    emit(["status": dispatchAttempted ? "uncertain" : (args.first == "continue" ? "refused" : "unavailable"), "reason": "accessibility_probe_failed", "dispatch_attempted": dispatchAttempted])
    exit(1)
}
