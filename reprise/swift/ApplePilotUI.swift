import Foundation
import Combine
import CryptoKit
import Darwin

/// One-time application of the operator's activation request. Subsequent changes persist.
enum StartupPreferences {
    static let versionKey = "StartupActivationVersion"
    static let pilotEnabledKey = "ApplePilotEnabled"
    static func migrate(_ defaults: UserDefaults) {
        guard defaults.integer(forKey: versionKey) < 1 else { return }
        defaults.set(true, forKey: "DesktopLinkedStartup")
        defaults.set(true, forKey: "SharedCodexStartup")
        defaults.set(true, forKey: pilotEnabledKey)
        defaults.set(1, forKey: versionKey)
    }
    static func startupFlag(_ key: String, defaults: UserDefaults = .standard) -> Bool {
        migrate(defaults)
        return defaults.bool(forKey: key)
    }
}

/// Host-owned analysis template. The operator request remains untrusted data.
enum PreparationAnalysisTemplate {
    static let objective = "Analyse locale de préparation (reprise-analysis-v3)."
    static func proposal(_ request: String) -> [String: Any]? {
        guard !request.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty,
              request.utf8.count <= 1024 else { return nil }
        guard let data = try? JSONSerialization.data(withJSONObject: ["request": request, "template": "reprise-analysis-v3"], options: [.sortedKeys, .withoutEscapingSlashes]),
              data.count <= 1024, let encoded = String(data: data, encoding: .utf8) else { return nil }
        return ["objective": objective, "acceptance": ["Une proposition de mission et ses seuls blocages, à relire par l’opérateur."],
                "constraints": [encoded], "questions": [String]()]
    }
}

private struct PreparationLink: Codable, Equatable {
    let project: String
    let work: String
    let analysis: String
    let mission: String?
    var valid: Bool {
        [project, work, analysis].allSatisfy { UUID(uuidString: $0)?.uuidString.lowercased() == $0 }
            && (mission == nil || (UUID(uuidString: mission!)?.uuidString.lowercased() == mission && mission != analysis))
    }
}

/// Crash-durable identity marker written before any preparation request is emitted.
private struct PreparationIntent: Codable, Equatable {
    let project: String
    let operation: String
    let kind: String
    let revision: Int
    let fingerprint: String
    let attempt: String?
    let authorizationID: String?
    let contextSHA256: String?
    let work: String?
    let review: [String: String]?

    var validReview: Bool {
        if kind == "memory_attach" {
            guard let review, let work, UUID(uuidString: work)?.uuidString.lowercased() == work,
                  Set(review.keys) == Set(["mutation", "snapshot", "expected"]),
                  UUID(uuidString: review["mutation"] ?? "")?.uuidString.lowercased() == review["mutation"],
                  let expected = Int(review["expected"] ?? ""), expected >= 0, expected < Int.max,
                  revision == expected + 1, review["snapshot"] == fingerprint,
                  fingerprint.count == 64, fingerprint.allSatisfy({ $0.isHexDigit && !$0.isUppercase }) else { return false }
            return true
        }

        guard ["candidate_import", "clarify", "clarification_review", "memory_attach"].contains(kind) else { return review == nil }
        guard let review, let work, UUID(uuidString: work)?.uuidString.lowercased() == work else { return false }
        let ids = kind == "candidate_import" ? ["source", "run", "mutation"] : ["question", "mutation"]
        let hashes = kind == "candidate_import" ? ["response"] : [kind == "clarification_review" ? "reason" : "answer"]
        let keys = Set(ids + hashes + (kind == "clarify" ? ["resolved", "expected"] : kind == "clarification_review" ? ["action", "expected"] : []))
        if kind == "clarification_review" {
            guard let expected = Int(review["expected"] ?? ""), expected >= 0, expected < Int.max,
                  revision == expected + 1, review["reason"] == fingerprint,
                  ["dismiss", "reopen"].contains(review["action"] ?? "") else { return false }
        }
        if kind == "clarify" {
            guard let expected = Int(review["expected"] ?? ""), expected >= 0, expected < Int.max,
                  revision == expected + 1, review["answer"] == fingerprint else { return false }
        }
        return Set(review.keys) == keys
            && ids.allSatisfy { UUID(uuidString: review[$0] ?? "")?.uuidString.lowercased() == review[$0] }
            && hashes.allSatisfy { (review[$0] ?? "").count == 64 && review[$0]!.allSatisfy { $0.isHexDigit && !$0.isUppercase } }
            && (kind != "clarify" || (["true", "false"].contains(review["resolved"] ?? "") && Int(review["expected"] ?? "") != nil))
    }

    init(project: String, operation: String, kind: String, revision: Int, fingerprint: String, attempt: String? = nil, authorizationID: String? = nil, contextSHA256: String? = nil, work: String? = nil, review: [String: String]? = nil) {
        self.project = project; self.operation = operation; self.kind = kind
        self.revision = revision; self.fingerprint = fingerprint; self.attempt = attempt
        self.authorizationID = authorizationID; self.contextSHA256 = contextSHA256; self.work = work; self.review = review
    }
}

private final class PreparationIntentJournal {
    enum JournalError: Error { case invalidDirectory, unsafeFile, writeFailed, identityConflict }
    private let state: URL
    private let folderName = "apple-pilot-intents.noindex"
    private let syncFD: (Int32) -> Int32
    private let unlinkMarker: (Int32, String) -> Int32

    init(state: URL, syncFD: @escaping (Int32) -> Int32 = { Darwin.fsync($0) },
         unlinkMarker: @escaping (Int32, String) -> Int32 = { Darwin.unlinkat($0, $1, 0) }) {
        self.state = state
        self.syncFD = syncFD
        self.unlinkMarker = unlinkMarker
    }

    func read(project: String) throws -> PreparationIntent? {
        try withFolder(create: false) { folder in try readIntent(folder: folder, project: project) }
    }

    private struct TerminalReceipt: Codable {
        let intent: PreparationIntent
        let effect: String
        let status: String
    }

    // The receipt contains identity and a protocol status only, never proposal text.
    func recordTerminal(_ intent: PreparationIntent, effect: String, status: String) throws {
        guard ["finished", "not_admitted"].contains(effect), !status.isEmpty,
              status.count <= 64, status.allSatisfy({ $0.isASCII && ($0.isLetter || $0 == "_") }) else {
            throw JournalError.unsafeFile
        }
        try withFolder(create: true) { folder in
            try withLock(folder: folder) {
                guard try readIntent(folder: folder, project: intent.project) == intent else {
                    throw JournalError.identityConflict
                }
                try atomicWrite(TerminalReceipt(intent: intent, effect: effect, status: status),
                                folder: folder, name: terminalName(intent.project))
            }
        }
    }

    private func terminalRecord(project: String) throws -> TerminalReceipt? {
        try withFolder(create: false) { folder in
            guard folder >= 0 else { return nil }
            return try withLock(folder: folder) {
                let fd = Darwin.openat(folder, terminalName(project), O_RDONLY | O_NOFOLLOW | O_NONBLOCK)
                if fd < 0 && errno == ENOENT { return nil }
                guard fd >= 0 else { throw JournalError.unsafeFile }
                defer { _ = Darwin.close(fd) }
                try checkFile(fd)
                var data = Data(), buffer = [UInt8](repeating: 0, count: 1024)
                while true {
                    let count = Darwin.read(fd, &buffer, buffer.count)
                    if count < 0 && errno == EINTR { continue }
                    guard count >= 0, data.count + count <= 4096 else { throw JournalError.unsafeFile }
                    if count == 0 { break }
                    data.append(buffer, count: count)
                }
                let receipt = try JSONDecoder().decode(TerminalReceipt.self, from: data)
                let intent = receipt.intent
                guard intent.project == project,
                      intent.attempt == nil || UUID(uuidString: intent.attempt!)?.uuidString.lowercased() == intent.attempt,
                      UUID(uuidString: intent.operation)?.uuidString.lowercased() == intent.operation,
                      ["prepare", "revise", "authorize", "authorize_local", "local_start", "candidate_import", "clarify", "clarification_review", "memory_attach"].contains(intent.kind), intent.revision >= 0, intent.validReview,
                      !["authorize", "authorize_local", "local_start"].contains(intent.kind) || (intent.authorizationID != nil && intent.work != nil && UUID(uuidString: intent.authorizationID ?? "")?.uuidString.lowercased() == intent.authorizationID && UUID(uuidString: intent.work ?? "")?.uuidString.lowercased() == intent.work && intent.contextSHA256?.count == 64 && intent.contextSHA256!.allSatisfy({ $0.isHexDigit && !$0.isUppercase })),
                      intent.fingerprint.count == 64,
                      intent.fingerprint.allSatisfy({ $0.isHexDigit && !$0.isUppercase }),
                      ["finished", "not_admitted"].contains(receipt.effect), !receipt.status.isEmpty,
                      receipt.status.count <= 64,
                      receipt.status.allSatisfy({ $0.isASCII && ($0.isLetter || $0 == "_") }) else {
                    throw JournalError.unsafeFile
                }
                // Retry durability after a previous rename followed by failed fsync.
                guard syncFD(fd) == 0, syncFD(folder) == 0 else { throw JournalError.writeFailed }
                return receipt
            }
        }
    }

    func terminal(project: String) throws -> PreparationIntent? {
        try terminalRecord(project: project)?.intent
    }
    func refusedImport(project: String, operation: String) throws -> PreparationIntent? {
        guard let receipt = try terminalRecord(project: project), receipt.effect == "not_admitted",
              receipt.intent.operation == operation, receipt.intent.kind == "candidate_import" else { return nil }
        return receipt.intent
    }

    func write(_ intent: PreparationIntent) throws {
        guard intent.validReview, intent.revision >= 0,
              ["prepare", "revise", "authorize", "authorize_local", "local_start", "candidate_import", "clarify", "clarification_review", "memory_attach"].contains(intent.kind),
              intent.work == nil || UUID(uuidString: intent.work!)?.uuidString.lowercased() == intent.work else { throw JournalError.unsafeFile }
        try withFolder(create: true) { folder in
            try withLock(folder: folder) {
                if let existing = try readIntent(folder: folder, project: intent.project), existing != intent {
                    throw JournalError.identityConflict
                }
                // Retrying the same identity/proposal is a new attempt: an old
                // refusal must never resolve it. Retain the old receipt as history.
                if Darwin.renameat(folder, terminalName(intent.project), folder,
                                   ".terminal-history-" + UUID().uuidString.lowercased()) != 0 && errno != ENOENT {
                    throw JournalError.writeFailed
                }
                guard syncFD(folder) == 0 else { throw JournalError.writeFailed }
                try atomicWrite(intent, folder: folder, name: markerName(intent.project))
            }
        }
    }

    func link(project: String, work: String) throws -> PreparationLink? {
        try withFolder(create: false) { folder in
            guard folder >= 0 else { return nil }
            return try withLock(folder: folder) { try readLink(folder: folder, project: project, work: work) }
        }
    }
    private func readLink(folder: Int32, project: String, work: String) throws -> PreparationLink? {
        guard UUID(uuidString: project)?.uuidString.lowercased() == project,
              UUID(uuidString: work)?.uuidString.lowercased() == work else { throw JournalError.unsafeFile }
        let fd = Darwin.openat(folder, "link-\(project)-\(work).json", O_RDONLY | O_NOFOLLOW | O_NONBLOCK)
        if fd < 0 && errno == ENOENT { return nil }
        guard fd >= 0 else { throw JournalError.unsafeFile }
        defer { _ = Darwin.close(fd) }
        try checkFile(fd)
        var data = Data(), buffer = [UInt8](repeating: 0, count: 512)
        while true {
            let count = Darwin.read(fd, &buffer, buffer.count)
            if count < 0 && errno == EINTR { continue }
            guard count >= 0, data.count + count <= 1024 else { throw JournalError.unsafeFile }
            if count == 0 { break }; data.append(buffer, count: count)
        }
        let object = try JSONSerialization.jsonObject(with: data) as? [String: Any]
        guard let object, [Set(["project", "work", "analysis"]), Set(["project", "work", "analysis", "mission"])].contains(Set(object.keys)) else { throw JournalError.unsafeFile }
        let link = try JSONDecoder().decode(PreparationLink.self, from: data)
        guard link.valid, link.project == project, link.work == work else { throw JournalError.unsafeFile }
        return link
    }
    func writeLink(_ link: PreparationLink) throws {
        guard link.valid else { throw JournalError.unsafeFile }
        try withFolder(create: true) { folder in
            try withLock(folder: folder) {
                if let old = try readLink(folder: folder, project: link.project, work: link.work),
                   old.analysis != link.analysis || (old.mission != nil && old.mission != link.mission) {
                    throw JournalError.identityConflict
                }
                try atomicWrite(link, folder: folder, name: "link-\(link.project)-\(link.work).json")
            }
        }
    }

    private func terminalName(_ project: String) -> String { "terminal-\(project).json" }

    private func atomicWrite<T: Encodable>(_ value: T, folder: Int32, name: String) throws {
        let temporary = ".intent-" + UUID().uuidString.lowercased()
        let fd = Darwin.openat(folder, temporary, O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW, mode_t(0o600))
        guard fd >= 0 else { throw JournalError.writeFailed }
        defer { _ = Darwin.close(fd); _ = Darwin.unlinkat(folder, temporary, 0) }
        let encoder = JSONEncoder(); encoder.outputFormatting = [.sortedKeys]
        let data = try encoder.encode(value)
        try data.withUnsafeBytes { raw in
            guard let base = raw.baseAddress else { throw JournalError.writeFailed }
            var written = 0
            while written < raw.count {
                let count = Darwin.write(fd, base.advanced(by: written), raw.count - written)
                if count < 0 && errno == EINTR { continue }
                guard count > 0 else { throw JournalError.writeFailed }
                written += count
            }
        }
        guard syncFD(fd) == 0 else { throw JournalError.writeFailed }
        guard Darwin.renameat(folder, temporary, folder, name) == 0 else { throw JournalError.writeFailed }
        guard syncFD(folder) == 0 else { throw JournalError.writeFailed }
    }

    func clear(_ intent: PreparationIntent) -> Bool {
        do {
            return try withFolder(create: false) { folder in
                if folder < 0 { return true }
                return try withLock(folder: folder) {
                    guard let existing = try readIntent(folder: folder, project: intent.project) else { return syncFD(folder) == 0 }
                    guard existing == intent else { return false }
                    guard unlinkMarker(folder, markerName(intent.project)) == 0 else { return false }
                    return syncFD(folder) == 0
                }
            }
        } catch { return false }
    }

    private func markerName(_ project: String) -> String { "intent-\(project).json" }

    private func withFolder<T>(create: Bool, _ body: (Int32) throws -> T) throws -> T {
        let root = Darwin.open(state.path, O_RDONLY | O_DIRECTORY | O_NOFOLLOW)
        guard root >= 0 else { throw JournalError.invalidDirectory }
        defer { _ = Darwin.close(root) }
        try checkDirectory(root)
        if create {
            let created = Darwin.mkdirat(root, folderName, mode_t(0o700))
            if created != 0 && errno != EEXIST {
                throw JournalError.invalidDirectory
            }
            // Also sync after EEXIST: a previous mkdir may have succeeded while
            // its parent fsync failed, so the directory is not yet proven durable.
            guard syncFD(root) == 0 else { throw JournalError.writeFailed }
        }
        let folder = Darwin.openat(root, folderName, O_RDONLY | O_DIRECTORY | O_NOFOLLOW)
        if folder < 0 && !create && errno == ENOENT { return try body(-1) }
        guard folder >= 0 else { throw JournalError.invalidDirectory }
        defer { _ = Darwin.close(folder) }
        try checkDirectory(folder)
        return try body(folder)
    }

    private func checkDirectory(_ fd: Int32) throws {
        var info = stat()
        guard Darwin.fstat(fd, &info) == 0, (info.st_mode & mode_t(S_IFMT)) == mode_t(S_IFDIR),
              info.st_uid == getuid(), (info.st_mode & mode_t(0o077)) == 0 else {
            throw JournalError.invalidDirectory
        }
    }

    private func withLock<T>(folder: Int32, _ body: () throws -> T) throws -> T {
        let fd = Darwin.openat(folder, "lock", O_RDWR | O_CREAT | O_NOFOLLOW, mode_t(0o600))
        guard fd >= 0 else { throw JournalError.unsafeFile }
        defer { _ = Darwin.close(fd) }
        try checkFile(fd)
        var lock = flock()
        lock.l_type = Int16(F_WRLCK)
        lock.l_whence = Int16(SEEK_SET)
        // This runs on the main actor. Never wait indefinitely for a lock held
        // by another or suspended process; fail closed and let the operator retry.
        guard Darwin.fcntl(fd, F_SETLK, &lock) == 0 else { throw JournalError.writeFailed }
        defer {
            lock.l_type = Int16(F_UNLCK)
            _ = Darwin.fcntl(fd, F_SETLK, &lock)
        }
        return try body()
    }

    private func checkFile(_ fd: Int32) throws {
        var info = stat()
        guard Darwin.fstat(fd, &info) == 0, (info.st_mode & mode_t(S_IFMT)) == mode_t(S_IFREG),
              info.st_uid == getuid(), info.st_nlink == 1, (info.st_mode & mode_t(0o077)) == 0 else {
            throw JournalError.unsafeFile
        }
    }

    private func readIntent(folder: Int32, project: String) throws -> PreparationIntent? {
        guard folder >= 0 else { return nil }
        let fd = Darwin.openat(folder, markerName(project), O_RDONLY | O_NOFOLLOW | O_NONBLOCK)
        if fd < 0 && errno == ENOENT { return nil }
        guard fd >= 0 else { throw JournalError.unsafeFile }
        defer { _ = Darwin.close(fd) }
        try checkFile(fd)
        var data = Data()
        var buffer = [UInt8](repeating: 0, count: 128)
        while true {
            let count = Darwin.read(fd, &buffer, buffer.count)
            if count < 0 && errno == EINTR { continue }
            guard count >= 0, data.count + count <= 2048 else { throw JournalError.unsafeFile }
            if count == 0 { break }
            data.append(buffer, count: count)
        }
        let object = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any]
        if let object, [Set(["project", "operation", "kind", "revision", "fingerprint"]), Set(["project", "operation", "kind", "revision", "fingerprint", "attempt"]), Set(["project", "operation", "kind", "revision", "fingerprint", "attempt", "authorizationID", "contextSHA256", "work"]), Set(["project", "operation", "kind", "revision", "fingerprint", "attempt", "work", "review"])].contains(Set(object.keys)),
           let intent = try? JSONDecoder().decode(PreparationIntent.self, from: data),
           intent.project == project,
           intent.attempt == nil || UUID(uuidString: intent.attempt!)?.uuidString.lowercased() == intent.attempt,
           UUID(uuidString: intent.operation)?.uuidString.lowercased() == intent.operation,
           ["prepare", "revise", "authorize", "authorize_local", "local_start", "candidate_import", "clarify", "clarification_review", "memory_attach"].contains(intent.kind), intent.revision >= 0, intent.validReview,
                      !["authorize", "authorize_local", "local_start"].contains(intent.kind) || (intent.authorizationID != nil && intent.work != nil && UUID(uuidString: intent.authorizationID ?? "")?.uuidString.lowercased() == intent.authorizationID && UUID(uuidString: intent.work ?? "")?.uuidString.lowercased() == intent.work && intent.contextSHA256?.count == 64 && intent.contextSHA256!.allSatisfy({ $0.isHexDigit && !$0.isUppercase })),
           intent.fingerprint.count == 64,
           intent.fingerprint.allSatisfy({ $0.isHexDigit && !$0.isUppercase }) {
            return intent
        }
        // A prior journal only stored the UUID. Keep it recoverable, but never
        // treat it as evidence that a particular mutation has completed.
        if let text = String(data: data, encoding: .utf8) {
            let parts = text.split(separator: "\n", omittingEmptySubsequences: false)
            if parts.count == 3, String(parts[0]) == project, parts[2].isEmpty,
               let operation = UUID(uuidString: String(parts[1])),
               operation.uuidString.lowercased() == String(parts[1]) {
                return PreparationIntent(project: project, operation: operation.uuidString.lowercased(),
                                         kind: "legacy", revision: -1, fingerprint: "")
            }
        }
        throw JournalError.unsafeFile
    }
}

/// Isolated UI controller. The caller supplies trusted bundled executable paths.
@MainActor
final class ApplePilotUI: ObservableObject {
    private struct PendingPreparation {
        let project: String
        let operation: String
        let intent: PreparationIntent?
        let selection: UUID
    }

    struct Message: Identifiable {
        let id = UUID()
        let role: String
        let text: String
    }

    @Published var enabled = false { didSet { preferences?.set(enabled, forKey: StartupPreferences.pilotEnabledKey) } }
    @Published var showMemory = false
    @Published var projectID = "" { didSet { saveSelection(projectID, key: "ApplePilotProject", uuid: true); invalidateOperationBrowser(); selectPreparationProject(projectID); clearConsentContext(); restoreAnalysisLink() } }
    @Published var workID = "" { didSet { saveSelection(workID, key: "ApplePilotWork", uuid: true); invalidateOperationBrowser(); clearConsentContext(); restoreAnalysisLink() } }
    @Published var checkpointID = "" { didSet { saveSelection(checkpointID, key: "ApplePilotCheckpoint", uuid: true) } }
    @Published var skill = "checkpoint-review" { didSet { saveSelection(skill, key: "ApplePilotSkill", uuid: false) } }
    @Published var prompt = ""
    @Published private var importRefusalAvailable = false
    @Published var analysisRequest = ""
    @Published private var analysisLink: PreparationLink?
    @Published private(set) var candidateResult: [String: Any] = [:]
    @Published private var clarificationDrafts: [String: String] = [:]
    private var candidateSelections: [String: [String]] = [:]
    @Published private(set) var operationRows: [[String: Any]] = []
    @Published private(set) var operationBrowserBusy = false
    @Published private(set) var operationBrowserStatus = "Liste non chargée."
    @Published private(set) var operationOffset = 0
    @Published private(set) var operationNextOffset: Int?
    @Published var operationFilterWork = false { didSet { invalidateOperationBrowser() } }
    @Published var selectedExistingOperation = ""
    private var operationSelection = UUID()
    private var browserRequests: [String: (String, String, UUID, Int)] = [:]
    private var browserOperationKind = ""
    private var browserOperationID = ""
    private func invalidateOperationBrowser() {
        clearRecallDraft()
        operationSelection = UUID(); operationRows = []; selectedExistingOperation = ""
        operationBrowserBusy = false; operationOffset = 0; operationNextOffset = nil
        operationBrowserStatus = "Contexte changé ; rechargez la liste."
        browserOperationKind = ""; browserOperationID = ""
    }
    func browseOperations(offset: Int = 0) {
        guard enabled, canSelect, canonical(projectID), !operationBrowserBusy,
              !operationFilterWork || canonical(workID), startHost() else { return }
        operationBrowserBusy = true
        let id = request("job_list", fields: ["enabled": true, "project_id": projectID,
            "work_id": operationFilterWork ? workID : NSNull(), "limit": 20, "offset": offset])
        browserRequests[id] = (projectID, workID, operationSelection, offset)
        operationBrowserStatus = "Chargement des opérations…"
    }
    var canOpenExistingOperation: Bool {
        canSwitchReview && !operationBrowserBusy && canonical(selectedExistingOperation)
            && operationRows.contains { $0["operation_id"] as? String == selectedExistingOperation
                && $0["project_id"] as? String == projectID && $0["selectable"] as? Bool == true }
    }
    func openExistingOperation() {
        guard canSwitchReview, !operationBrowserBusy,
              let row = operationRows.first(where: { $0["operation_id"] as? String == selectedExistingOperation }),
              row["project_id"] as? String == projectID, row["selectable"] as? Bool == true,
              canonical(selectedExistingOperation) else { return }
        // Re-read the durable journal before changing selection, including markers
        // created by another GUI instance since the last visible snapshot.
        restorePreparationIntent(projectID)
        guard canSwitchReview else { return }
        let association = row["work_association"] as? String
        guard association == "bound" || association == "unbound",
              association != "bound" || (row["work_id"] as? String).map(canonical) == true else { return }
        let operation = selectedExistingOperation
        let kind = row["kind"] as? String ?? "unknown"
        if let work = row["work_id"] as? String, canonical(work) { workID = work }
        selectOperation(operation)
        browserOperationID = operation; browserOperationKind = kind
        loadPreparation()
    }
    @Published var recallQuery = ""
    @Published var recallSelected: Set<String> = [] { didSet { clearRecallPreview() } }
    @Published var recallAcknowledged = false
    @Published private(set) var recallPreview: [String: Any] = [:]
    @Published private(set) var recallPreviewSHA256 = ""
    private var memoryReadSelections: [String: (String, String, UUID)] = [:]
    private var recallPreviewSelection: [String: (String, String, String, UUID, Set<String>)] = [:]
    private func clearRecallPreview() {
        recallPreview = [:]; recallPreviewSHA256 = ""; recallAcknowledged = false
    }
    private func clearRecallDraft() {
        recallSelected = []; recallQuery = ""; clearRecallPreview()
    }
    func searchRecallFacts(offset: Int = 0) {
        guard !recallQuery.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty,
              recallQuery.utf8.count <= 1024 else { return }
        memoryCall("search_facts", fields: ["query": recallQuery, "offset": offset])
    }
    func previewRecall() {
        guard preparationCanEdit, isAnalysis, canonical(workID), recallSelected.count <= 8, startHost() else { return }
        preparationBusy = true; clearRecallPreview()
        let id = request("job_memory_preview", fields: ["enabled": true, "project_id": projectID,
            "work_id": workID, "operation_id": preparationOperationID, "fact_ids": recallSelected.sorted()])
        recallPreviewSelection[id] = (projectID, workID, preparationOperationID, operationSelection, recallSelected)
    }
    var canAttachRecall: Bool {
        preparationCanEdit && isAnalysis && recallAcknowledged && !recallPreview.isEmpty
            && recallPreviewSHA256.count == 64 && recallPreview["project_id"] as? String == projectID
            && recallPreview["work_id"] as? String == workID
    }
    func attachRecall() {
        guard canAttachRecall, let revision = preparationJob["revision"] as? Int, startHost() else { return }
        preparationBusy = true
        request("job_memory_attach", fields: ["enabled": true, "project_id": projectID, "work_id": workID,
            "operation_id": preparationOperationID, "fact_ids": recallSelected.sorted(),
            "snapshot_sha256": recallPreviewSHA256, "selection_id": UUID().uuidString.lowercased(),
            "expected_revision": revision, "operator_confirmed": true])
    }
    var attachedRecallFacts: [[String: Any]] {
        ((preparationJob["memory_selection"] as? [String: Any])?["snapshot"] as? [String: Any])?["facts"] as? [[String: Any]] ?? []
    }
    @Published var consentBackend = "codex"
    @Published var localMaxSeconds = 60 { didSet { localPlan = [:] } }
    @Published private(set) var localPlan: [String: Any] = [:]
    private var localSelections: [String: [String]] = [:]
    @Published var proposalObjective = ""
    @Published var proposalAcceptance = ""
    @Published var proposalConstraints = ""
    @Published var proposalQuestions = ""
    @Published private(set) var preparationOperationID = ""
    @Published private(set) var preparationStatus = "Aucune préparation chargée."
    @Published private(set) var preparationJob: [String: Any] = [:]
    @Published private(set) var consentContext: [String: Any] = [:]
    @Published private(set) var consentContextSHA256 = ""
    private var consentSelections: [String: [String]] = [:]
    @Published private(set) var preparationBusy = false
    @Published private(set) var preparationUncertain = false
    @Published private(set) var preparationJournalUnavailable = false
    @Published private(set) var status = "Pilote arrêté."
    @Published private(set) var messages: [Message] = []
    @Published private(set) var checkpoints: [[String: Any]] = []
    @Published private(set) var facts: [[String: Any]] = []
    @Published private(set) var factDetail: [String: Any] = [:]
    @Published private(set) var checkpointDetail: [String: Any] = [:]
    @Published private(set) var memoryIntents: [[String: Any]] = []
    @Published private(set) var memoryProject = ""
    @Published private(set) var memoryBusy = false
    @Published private(set) var memoryStatus = "Mémoire non liée."
    @Published private(set) var memoryOperationID = ""
    @Published private(set) var memoryOperationState = ""
    @Published private(set) var memoryOperationProject = ""
    @Published private(set) var memoryUncertain = false
    @Published private(set) var factsNextOffset: Int?
    @Published private(set) var checkpointsNextOffset: Int?
    @Published private(set) var intentsNextOffset: Int?
    @Published var memoryInspectOperationID = ""
    private struct MemoryDraft {
        var factText = ""
        var sourceKind = ""
        var sourceReference = ""
        var sourceProducer = ""
        var sourceObservedAt = ""
        var sourceSHA256 = ""
        var decisions = ""
        var constraints = ""
        var uncertainEffects = ""
        var nextAction = ""
        var selectedFactIDs: Set<String> = []
        var checkpointVersion = "0"
    }
    @Published private var draft = MemoryDraft()
    var factText: String {
        get { draft.factText }
        set { guard draftEditable || resettingDraft else { return }; draft.factText = newValue; if !newValue.isEmpty { adoptDraft() } }
    }
    var sourceKind: String {
        get { draft.sourceKind }
        set { guard draftEditable || resettingDraft else { return }; draft.sourceKind = newValue; if !newValue.isEmpty { adoptDraft() } }
    }
    var sourceReference: String {
        get { draft.sourceReference }
        set { guard draftEditable || resettingDraft else { return }; draft.sourceReference = newValue; if !newValue.isEmpty { adoptDraft() } }
    }
    var sourceProducer: String {
        get { draft.sourceProducer }
        set { guard draftEditable || resettingDraft else { return }; draft.sourceProducer = newValue; if !newValue.isEmpty { adoptDraft() } }
    }
    var sourceObservedAt: String {
        get { draft.sourceObservedAt }
        set { guard draftEditable || resettingDraft else { return }; draft.sourceObservedAt = newValue; if !newValue.isEmpty { adoptDraft() } }
    }
    var sourceSHA256: String {
        get { draft.sourceSHA256 }
        set { guard draftEditable || resettingDraft else { return }; draft.sourceSHA256 = newValue; if !newValue.isEmpty { adoptDraft() } }
    }
    var decisions: String {
        get { draft.decisions }
        set { guard checkpointEditable || resettingDraft else { return }; draft.decisions = newValue; if !newValue.isEmpty { adoptCheckpointDraft() } }
    }
    var constraints: String {
        get { draft.constraints }
        set { guard checkpointEditable || resettingDraft else { return }; draft.constraints = newValue; if !newValue.isEmpty { adoptCheckpointDraft() } }
    }
    var uncertainEffects: String {
        get { draft.uncertainEffects }
        set { guard checkpointEditable || resettingDraft else { return }; draft.uncertainEffects = newValue; if !newValue.isEmpty { adoptCheckpointDraft() } }
    }
    var nextAction: String {
        get { draft.nextAction }
        set { guard checkpointEditable || resettingDraft else { return }; draft.nextAction = newValue; if !newValue.isEmpty { adoptCheckpointDraft() } }
    }
    var selectedFactIDs: Set<String> {
        get { draft.selectedFactIDs }
        set { guard checkpointEditable || resettingDraft else { return }; draft.selectedFactIDs = newValue; if !newValue.isEmpty { adoptCheckpointDraft() } }
    }
    var checkpointVersion: String {
        get { draft.checkpointVersion }
        set { guard checkpointEditable || resettingDraft else { return }; draft.checkpointVersion = newValue; if newValue != "0" { adoptCheckpointDraft() } }
    }
    @Published private(set) var draftProjectID = ""
    @Published private(set) var checkpointDraftWorkID = ""
    @Published private(set) var sessionOpen = false
    @Published private(set) var busy = false
    @Published private(set) var closing = false
    @Published private(set) var cancelling = false
    @Published private(set) var cleanupUnconfirmed = false

    private let preferences: UserDefaults?
    private var knownProjects: Set<String>?
    private var preparationProjectID = ""
    private var preparationPending: [String: PendingPreparation] = [:]
    private var restoredContext: [String]?
    private var startupOpenAttempted = false
    private let validSkills: Set<String> = ["checkpoint-review", "project-summary"]
    private var resettingDraft = false
    private let python: URL
    private let service: URL
    private let state: URL
    private let preparationJournal: PreparationIntentJournal
    private let requestTimeout: TimeInterval
    private var process: Process?
    private var hostTerminating = false
    private var memoryRecoveryHost = false
    private var input: FileHandle?
    private let ioQueue = DispatchQueue(label: "local.reprise.apple-pilot.io")
    private let readQueue = DispatchQueue(label: "local.reprise.apple-pilot.read")
    private var pending: [String: String] = [:]
    private var memoryPending: [String: String] = [:]
    private var memoryCheckIDs: [String: String] = [:]
    private var mutationPendingID: String?
    private var bindTarget = ""
    private var turnID: String?
    private var cancelTurnIDs: [String: String] = [:]
    private var generation = UUID()
    private var exitCode: Int32?
    private var readEnded = false
    nonisolated private static let maximumFrame = 131_072

    init(python: URL, service: URL, state: URL, requestTimeout: TimeInterval = 90,
         preferences: UserDefaults? = nil, journalSync: @escaping (Int32) -> Int32 = { Darwin.fsync($0) }) {
        self.preferences = preferences
        self.python = python
        self.service = service
        self.state = state
        self.preparationJournal = PreparationIntentJournal(state: state, syncFD: journalSync)
        self.requestTimeout = max(0.1, requestTimeout)
        if let preferences {
            StartupPreferences.migrate(preferences)
            enabled = preferences.bool(forKey: StartupPreferences.pilotEnabledKey)
            let project = preferences.string(forKey: "ApplePilotProject") ?? ""
            let work = preferences.string(forKey: "ApplePilotWork") ?? ""
            let checkpoint = preferences.string(forKey: "ApplePilotCheckpoint") ?? ""
            let prepOperation = preferences.string(forKey: "ApplePilotPreparationOperation") ?? ""
            let prepProject = preferences.string(forKey: "ApplePilotPreparationProject") ?? ""
            if canonical(project) { projectID = project }
            if canonical(work) { workID = work }
            if preferences.bool(forKey: "ApplePilotCheckpointInvalid") {
                checkpointID = checkpoint.isEmpty || canonical(checkpoint) ? "invalid" : checkpoint
            } else if canonical(checkpoint) { checkpointID = checkpoint }
            if let savedSkill = preferences.string(forKey: "ApplePilotSkill"), validSkills.contains(savedSkill) { skill = savedSkill }
            restoredContext = [projectID, workID, checkpointID, skill]
            if canonical(prepOperation) && canonical(prepProject) {
                var operations = preferences.dictionary(forKey: "ApplePilotPreparationOperations") as? [String: String] ?? [:]
                operations[prepProject] = prepOperation
                preferences.set(operations, forKey: "ApplePilotPreparationOperations")
                if prepProject == projectID {
                    preparationOperationID = prepOperation
                    preparationProjectID = prepProject
                    preparationUncertain = (preferences.dictionary(forKey: "ApplePilotPreparationUncertainOperations") as? [String: String])?[prepProject] == prepOperation
                }
            }
            if canonical(projectID) { restorePreparationIntent(projectID) }
            restoreAnalysisLink()
        }
    }

    private func saveSelection(_ value: String, key: String, uuid: Bool) {
        guard let preferences else { return }
        if key == "ApplePilotCheckpoint" {
            if value.isEmpty {
                preferences.removeObject(forKey: key)
                preferences.removeObject(forKey: "ApplePilotCheckpointInvalid")
            } else if canonical(value) {
                preferences.set(value, forKey: key)
                preferences.removeObject(forKey: "ApplePilotCheckpointInvalid")
            } else {
                preferences.set(true, forKey: "ApplePilotCheckpointInvalid")
                preferences.set(value, forKey: key)
            }
            return
        }
        if value.isEmpty { preferences.removeObject(forKey: key); return }
        guard uuid ? canonical(value) : validSkills.contains(value) else {
            preferences.removeObject(forKey: key)
            return
        }
        preferences.set(value, forKey: key)
    }

    func updateKnownProjects(_ ids: Set<String>) { knownProjects = ids }
    func openRestoredSessionIfReady() {
        guard !startupOpenAttempted, let restoredContext,
              restoredContext == [projectID, workID, checkpointID, skill], canOpen else { return }
        startupOpenAttempted = true
        open()
    }
    var contextNeedsConfiguration: Bool {
        !canonical(projectID) || !canonical(workID) || (knownProjects.map { !$0.contains(projectID) } ?? (preferences != nil))
            || (!checkpointID.isEmpty && !canonical(checkpointID)) || !validSkills.contains(skill)
    }
    var contextConfigurationMessage: String {
        if !checkpointID.isEmpty && !canonical(checkpointID) {
            return "Corrigez ou effacez l’identifiant du checkpoint."
        }
        return "Configuration requise : choisissez un projet disponible et un travail valide."
    }

    var hostRunning: Bool { process != nil }
    var canSelect: Bool { !sessionOpen && !busy && !memoryBusy && !preparationBusy && !closing && !cleanupUnconfirmed && !hostTerminating && !operationBrowserBusy
        && !["local_pending", "local_admitted", "local_running"].contains(preparationJob["status"] as? String ?? "") }
    var canOpen: Bool {
        enabled && canSelect && !contextNeedsConfiguration
    }
    var canSend: Bool {
        sessionOpen && !busy && !closing && !cleanupUnconfirmed
            && !prompt.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
            && prompt.utf8.count <= 8192
    }
    private func canonical(_ text: String) -> Bool {
        UUID(uuidString: text)?.uuidString.lowercased() == text
    }

    /// Explicit activation; merely creating or displaying this object does not start a process.
    private func startHost() -> Bool {
        if process != nil { return true }
        guard python.isFileURL, service.isFileURL, state.isFileURL,
              FileManager.default.isExecutableFile(atPath: python.path),
              FileManager.default.isReadableFile(atPath: service.path) else {
            status = "Le runtime ou le service expérimental est indisponible."; return false
        }
        let process = Process()
        let stdinPipe = Pipe()
        let stdoutPipe = Pipe()
        process.executableURL = python
        process.arguments = (python.path == "/usr/bin/env" ? ["python3"] : []) + ["-E", "-s", "-B", service.path, "--state", state.path]
        var env = ProcessInfo.processInfo.environment
        for key in env.keys where key.hasPrefix("PYTHON") || key.hasPrefix("DYLD_") { env.removeValue(forKey: key) }
        env["PYTHONNOUSERSITE"] = "1"
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        process.environment = env
        process.standardInput = stdinPipe
        process.standardOutput = stdoutPipe
        process.standardError = FileHandle.nullDevice
        let current = UUID()
        generation = current
        process.terminationHandler = { [weak self] child in
            Task { @MainActor [weak self] in self?.terminated(generation: current, code: child.terminationStatus) }
        }
        do {
            try process.run()
        } catch {
            status = "Impossible de démarrer le pilote expérimental."
            return false
        }
        self.process = process
        memoryRecoveryHost = cleanupUnconfirmed
        hostTerminating = false
        input = stdinPipe.fileHandleForWriting
        exitCode = nil
        readEnded = false
        readOutput(stdoutPipe.fileHandleForReading, generation: current)
        return true
    }

    func clearCheckpoints() {
        guard canSelect else { return }
        checkpoints = []
        checkpointID = ""
        checkpointDetail = [:]
        if memoryProject != projectID {
            memoryProject = ""
            facts = []
            factDetail = [:]
            checkpointDetail = [:]
            memoryIntents = []
        }
    }

    private func selectPreparationProject(_ project: String) {
        guard preparationProjectID != project else { return }
        preparationProjectID = project
        let operations = preferences?.dictionary(forKey: "ApplePilotPreparationOperations") as? [String: String] ?? [:]
        preparationOperationID = operations[project] ?? ""
        preparationJob = [:]
        proposalObjective = ""
        proposalAcceptance = ""
        proposalConstraints = ""
        proposalQuestions = ""
        preparationUncertain = uncertainPreparationOperation(for: project) == preparationOperationID && !preparationOperationID.isEmpty
        restorePreparationIntent(project)
        preparationStatus = preparationOperationID.isEmpty ? "Aucune préparation liée à ce projet." :
            (preparationJournalUnavailable ? "Journal local inaccessible ; aucune action ne sera envoyée." :
             preparationUncertain ? "Opération incertaine ; rechargez-la avant toute nouvelle action." : "Chargez la préparation enregistrée pour ce projet.")
    }

    private func restorePreparationIntent(_ project: String) {
        do {
            let active = try preparationJournal.read(project: project)
            if let terminal = try preparationJournal.terminal(project: project),
               active == terminal || (active == nil && (preparationOperationID.isEmpty || preparationOperationID == terminal.operation)) {
                preparationOperationID = terminal.operation
                preparationProjectID = project
                guard preparationJournal.clear(terminal) else { throw PreparationIntentJournal.JournalError.writeFailed }
                setPreparationUncertain(false, project: project, operation: terminal.operation)
                preparationUncertain = false
                preparationJournalUnavailable = false
                return
            }
            if let intent = active {
                if intent.kind == "candidate_import", let review = intent.review, let work = intent.work,
                   let source = review["source"] {
                    try preparationJournal.writeLink(PreparationLink(project: project, work: work, analysis: source, mission: intent.operation))
                    if project == projectID && work == workID { analysisLink = try preparationJournal.link(project: project, work: work) }
                }
                preparationOperationID = intent.operation
                preparationProjectID = project
                preparationUncertain = true
                preparationJournalUnavailable = false
            } else {
                preparationJournalUnavailable = false
            }
        } catch {
            preparationJournalUnavailable = true
            preparationUncertain = true
        }
    }

    private func uncertainPreparationOperation(for project: String) -> String? {
        (preferences?.dictionary(forKey: "ApplePilotPreparationUncertainOperations") as? [String: String])?[project]
    }

    private func clearPreparationIntent(_ intent: PreparationIntent, effect: String, status: String) -> Bool {
        do {
            try preparationJournal.recordTerminal(intent, effect: effect, status: status)
            guard preparationJournal.clear(intent) else { throw PreparationIntentJournal.JournalError.writeFailed }
            if preparationProjectID == intent.project { preparationJournalUnavailable = false }
            importRefusalAvailable = intent.kind == "candidate_import" && effect == "not_admitted"
            return true
        } catch {
            preparationJournalUnavailable = true
            setPreparationUncertain(true, project: intent.project, operation: intent.operation)
            return false
        }
    }

    private func setPreparationUncertain(_ uncertain: Bool, project: String, operation: String) {
        var values = preferences?.dictionary(forKey: "ApplePilotPreparationUncertainOperations") as? [String: String] ?? [:]
        if uncertain { values[project] = operation }
        else if values[project] == operation { values.removeValue(forKey: project) }
        preferences?.set(values, forKey: "ApplePilotPreparationUncertainOperations")
        if preparationProjectID == project && preparationOperationID == operation { preparationUncertain = uncertain }
    }

    private var preparationWorkMatches: Bool {
        guard let record = preparationJob["candidate_import"] as? [String: Any],
              let origin = record["origin"] as? [String: Any] else { return true }
        return origin["work_id"] as? String == workID && origin["project_id"] as? String == projectID
    }
    var preparationCanEdit: Bool { preparationWorkMatches && preparationJob["status"] as? String == "preparation" && !preparationBusy && !preparationUncertain && !preparationJournalUnavailable }

    func newPreparation() {
        guard enabled, canonical(projectID), !preparationBusy, !preparationUncertain,
              !preparationJournalUnavailable, !closing,
              !["local_pending", "local_admitted", "local_running"].contains(preparationJob["status"] as? String ?? "") else { return }
        do {
            if let intent = try preparationJournal.read(project: projectID) {
                preparationOperationID = intent.operation
                preparationProjectID = projectID
                preparationUncertain = true
                preparationStatus = "Une opération existe déjà pour ce projet ; rechargez-la avant d’en créer une autre."
                return
            }
        } catch {
            preparationJournalUnavailable = true
            preparationUncertain = true
            preparationStatus = "Journal local inaccessible ; aucune nouvelle opération n’a été créée."
            return
        }
        let operation = UUID().uuidString.lowercased()
        preparationOperationID = operation
        preparationProjectID = projectID
        preferences?.set(operation, forKey: "ApplePilotPreparationOperation")
        preferences?.set(projectID, forKey: "ApplePilotPreparationProject")
        var operations = preferences?.dictionary(forKey: "ApplePilotPreparationOperations") as? [String: String] ?? [:]
        operations[projectID] = operation
        preferences?.set(operations, forKey: "ApplePilotPreparationOperations")
        preparationJob = [:]
        proposalObjective = ""
        proposalAcceptance = ""
        proposalConstraints = ""
        proposalQuestions = ""
        preparationUncertain = false
        preparationStatus = "Nouvelle préparation locale ; aucune autorisation ni requête créée."
    }

    private func proposalPayload() -> [String: Any] {
        ["objective": proposalObjective,
         "acceptance": proposalAcceptance.components(separatedBy: .newlines).map { $0.trimmingCharacters(in: .whitespaces) }.filter { !$0.isEmpty },
         "constraints": proposalConstraints.components(separatedBy: .newlines).map { $0.trimmingCharacters(in: .whitespaces) }.filter { !$0.isEmpty },
         "questions": proposalQuestions.components(separatedBy: .newlines).map { $0.trimmingCharacters(in: .whitespaces) }.filter { !$0.isEmpty }]
    }

    private func canonicalProposalData(_ proposal: [String: Any]) -> Data? {
        try? JSONSerialization.data(withJSONObject: proposal, options: [.sortedKeys])
    }

    private func proposalFingerprint(_ proposal: [String: Any]) -> String? {
        guard let data = canonicalProposalData(proposal) else { return nil }
        return SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined()
    }

    func savePreparation() {
        guard enabled, canonical(projectID), preparationProjectID == projectID,
              canonical(preparationOperationID), !preparationBusy, !preparationUncertain,
              !preparationJournalUnavailable,
              !proposalObjective.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty,
              startHost() else { return }
        preparationBusy = true
        preparationStatus = "Enregistrement local de la préparation…"
        request("job_prepare", fields: ["enabled": true, "project_id": projectID,
            "operation_id": preparationOperationID, "proposal": proposalPayload()])
    }

    func loadPreparation() {
        guard enabled, canonical(projectID), canonical(preparationOperationID),
              preparationProjectID == projectID, !preparationBusy else { return }
        let wasUncertain = preparationUncertain || preparationJournalUnavailable
        restorePreparationIntent(projectID)
        if wasUncertain && !preparationUncertain && !preparationJournalUnavailable {
            preparationStatus = "Preuve terminale locale durable retrouvée ; aucune requête envoyée."
            return
        }
        guard enabled, canonical(projectID), canonical(preparationOperationID),
              preparationProjectID == projectID, !preparationBusy, startHost() else { return }
        preparationBusy = true
        preparationStatus = "Chargement de la préparation…"
        request("job_recover", fields: ["enabled": true, "project_id": projectID,
            "operation_id": preparationOperationID])
    }

    func revisePreparation() {
        guard preparationCanEdit, canonical(projectID), preparationProjectID == projectID,
              !preparationJournalUnavailable, let revision = preparationJob["revision"] as? Int else { return }
        let proposal = proposalPayload()
        guard let proposedData = canonicalProposalData(proposal),
              let existing = preparationJob["proposal"] as? [String: Any],
              let existingData = canonicalProposalData(existing) else {
            preparationStatus = "Proposition impossible à comparer de façon canonique ; aucune révision envoyée."
            return
        }
        guard proposedData != existingData else {
            preparationStatus = "Proposition identique ; aucune nouvelle révision ni requête créée."
            return
        }
        guard startHost() else { return }
        preparationBusy = true
        preparationStatus = "Révision locale avec contrôle de version…"
        request("job_revise", fields: ["enabled": true, "project_id": projectID,
            "operation_id": preparationOperationID, "proposal": proposal, "expected_revision": revision])
    }

    var analysisOperationID: String { analysisLink?.analysis ?? "" }
    var missionOperationID: String { analysisLink?.mission ?? "" }
    var isAnalysis: Bool { (!analysisOperationID.isEmpty && preparationOperationID == analysisOperationID) || (browserOperationID == preparationOperationID && browserOperationKind == "analysis") }
    var canSwitchReview: Bool {
        enabled && canSelect && !preparationUncertain && !preparationJournalUnavailable
            && !["local_pending", "local_admitted", "local_running"].contains(preparationJob["status"] as? String ?? "")
    }
    private func restoreAnalysisLink() {
        let previous = analysisLink
        candidateResult = [:]; importRefusalAvailable = false
        guard canonical(projectID), canonical(workID) else {
            analysisLink = nil
            if let previous, !preparationUncertain,
               preparationOperationID == previous.analysis || preparationOperationID == previous.mission { selectOperation("") }
            return
        }
        do {
            analysisLink = try preparationJournal.link(project: projectID, work: workID)
            if let previous, (previous.project != projectID || previous.work != workID), !preparationUncertain,
               preparationOperationID == previous.analysis || preparationOperationID == previous.mission {
                selectOperation(analysisLink?.mission ?? analysisLink?.analysis ?? "")
            }
            if isAnalysis { consentBackend = "foundation-models" }
            if let target = analysisLink?.mission {
                importRefusalAvailable = try preparationJournal.refusedImport(project: projectID, operation: target) != nil
            }
        }
        catch { analysisLink = nil; preparationJournalUnavailable = true; preparationUncertain = true }
    }
    private func saveAnalysisLink(_ link: PreparationLink) -> Bool {
        do { try preparationJournal.writeLink(link); analysisLink = link; return true }
        catch {
            preparationJournalUnavailable = true; preparationUncertain = true
            preparationStatus = "Liaison analyse/mission non durable ; aucune requête envoyée."
            return false
        }
    }
    private func selectOperation(_ operation: String) {
        clearRecallDraft()
        operationSelection = UUID(); browserOperationID = ""; browserOperationKind = ""
        preparationOperationID = operation; preparationProjectID = projectID
        preferences?.set(operation, forKey: "ApplePilotPreparationOperation")
        preferences?.set(projectID, forKey: "ApplePilotPreparationProject")
        var values = preferences?.dictionary(forKey: "ApplePilotPreparationOperations") as? [String: String] ?? [:]
        values[projectID] = operation; preferences?.set(values, forKey: "ApplePilotPreparationOperations")
        preparationJob = [:]; localPlan = [:]; consentContext = [:]; consentContextSHA256 = ""
        consentBackend = isAnalysis ? "foundation-models" : "codex"
        proposalObjective = ""; proposalAcceptance = ""; proposalConstraints = ""; proposalQuestions = ""
    }
    func showReviewOperation(analysis: Bool) {
        guard canSwitchReview else { return }
        let operation = analysis ? analysisOperationID : missionOperationID
        guard canonical(operation), startHost() else { return }
        selectOperation(operation); preparationBusy = true
        request("job_recover", fields: ["enabled": true, "project_id": projectID, "operation_id": operation])
    }
    func prepareAnalysis() {
        guard canSwitchReview, canonical(workID), analysisOperationID.isEmpty,
              let proposal = PreparationAnalysisTemplate.proposal(analysisRequest) else {
            preparationStatus = "Demande requise (1 Kio maximum). Une analyse existante se recharge ; un autre travail permet une autre analyse."
            return
        }
        newPreparation()
        guard !preparationUncertain, !preparationJournalUnavailable,
              saveAnalysisLink(PreparationLink(project: projectID, work: workID, analysis: preparationOperationID, mission: nil)) else { return }
        proposalObjective = proposal["objective"] as! String
        proposalAcceptance = (proposal["acceptance"] as! [String]).joined(separator: "\n")
        proposalConstraints = (proposal["constraints"] as! [String]).joined(separator: "\n"); proposalQuestions = ""
        consentBackend = "foundation-models"
        savePreparation()
    }
    func extractCandidate() {
        guard isAnalysis, !preparationBusy, !preparationUncertain, !preparationJournalUnavailable,
              let receipt = (preparationJob["local_execution"] as? [String: Any])?["receipt"] as? [String: Any],
              receipt["status"] as? String == "finished", let run = receipt["run_id"] as? String,
              let hash = receipt["response_sha256"] as? String, startHost() else { return }
        preparationBusy = true; candidateResult = [:]
        let id = request("job_candidate_extract", fields: ["enabled": true, "project_id": projectID,
            "operation_id": preparationOperationID, "work_id": workID, "run_id": run, "expected_response_sha256": hash])
        candidateSelections[id] = [projectID, workID, preparationOperationID, run, hash]
    }
    var candidateImportLabel: String {
        guard analysisLink?.mission != nil else { return "Créer explicitement la préparation de mission" }
        return importRefusalAvailable ? "Réessayer explicitement l’import refusé" : "Recharger la mission liée"
    }
    func importCandidate() {
        guard canSwitchReview, isAnalysis, let link = analysisLink,
              let origin = candidateResult["origin"] as? [String: Any],
              origin["project_id"] as? String == projectID, origin["work_id"] as? String == workID,
              origin["source_operation_id"] as? String == analysisOperationID,
              let run = origin["source_run_id"] as? String, let response = origin["response_sha256"] as? String,
              let hash = candidateResult["candidate_sha256"] as? String, startHost() else { return }
        let target = link.mission ?? UUID().uuidString.lowercased()
        // A retry is a new explicit operator action, allowed only after a durable
        // not_admitted receipt for this exact import. Unknown effects only recover.
        var mutation = UUID().uuidString.lowercased()
        if link.mission != nil {
            do {
                guard let refused = try preparationJournal.refusedImport(project: projectID, operation: target),
                      refused.work == workID, refused.fingerprint == hash,
                      refused.review?["source"] == link.analysis, refused.review?["run"] == run,
                      refused.review?["response"] == response, let priorMutation = refused.review?["mutation"] else {
                    showReviewOperation(analysis: false); return
                }
                mutation = priorMutation
            } catch {
                preparationJournalUnavailable = true; preparationUncertain = true; return
            }
        }
        selectOperation(target); preparationBusy = true
        request("job_candidate_import", fields: ["enabled": true, "project_id": projectID, "operation_id": target,
            "work_id": workID, "source_operation_id": link.analysis, "source_run_id": run,
            "expected_response_sha256": response, "expected_candidate_sha256": hash,
            "import_id": mutation, "expected_revision": NSNull(),
            "expected_proposal_sha256": NSNull(), "operator_confirmed": true])
    }
    var clarifications: [[String: Any]] { preparationJob["clarifications"] as? [[String: Any]] ?? [] }
    private func draftKey(_ id: String) -> String { projectID + "/" + workID + "/" + preparationOperationID + "/" + id }
    func clarificationAnswer(_ id: String) -> String {
        clarificationDrafts[draftKey(id)] ?? (clarifications.first { $0["id"] as? String == id }?["answer"] as? String ?? "")
    }
    func setClarificationAnswer(_ id: String, text: String) { clarificationDrafts[draftKey(id)] = text }
    func clarify(_ id: String, resolved: Bool) {
        let answer = clarificationAnswer(id)
        guard preparationCanEdit, canonical(workID), !answer.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty,
              answer.utf8.count <= 1024, let revision = preparationJob["revision"] as? Int,
              clarifications.contains(where: { $0["id"] as? String == id }), startHost() else { return }
        preparationBusy = true
        request("job_clarify", fields: ["enabled": true, "project_id": projectID,
            "operation_id": preparationOperationID, "question_id": id, "answer": answer, "resolved": resolved,
            "resolution_id": UUID().uuidString.lowercased(), "expected_revision": revision])
    }
    @Published var clarificationReasonDrafts: [String: String] = [:]
    func clarificationReason(_ id: String) -> String { clarificationReasonDrafts[draftKey(id)] ?? "" }
    func setClarificationReason(_ id: String, text: String) { clarificationReasonDrafts[draftKey(id)] = text }
    func reviewClarification(_ id: String, action: String) {
        let reason = clarificationReason(id)
        guard preparationCanEdit, canonical(workID), ["dismiss", "reopen"].contains(action),
              !reason.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty, reason.utf8.count <= 1024,
              let revision = preparationJob["revision"] as? Int,
              clarifications.contains(where: { $0["id"] as? String == id }), startHost() else { return }
        preparationBusy = true
        request("job_clarification_review", fields: ["enabled": true, "project_id": projectID,
            "operation_id": preparationOperationID, "question_id": id, "action": action, "reason": reason,
            "resolution_id": UUID().uuidString.lowercased(), "expected_revision": revision])
    }
    private func reviewProof(_ intent: PreparationIntent, job: [String: Any]) -> Bool {
        guard let review = intent.review, intent.validReview,
              (job["revision"] as? Int ?? -1) >= intent.revision else { return false }
        if intent.kind == "candidate_import", let record = job["candidate_import"] as? [String: Any],
           let origin = record["origin"] as? [String: Any] {
            return record["import_id"] as? String == review["mutation"]
                && record["candidate_sha256"] as? String == intent.fingerprint
                && origin["project_id"] as? String == intent.project && origin["work_id"] as? String == intent.work
                && origin["source_operation_id"] as? String == review["source"]
                && origin["source_run_id"] as? String == review["run"]
                && origin["response_sha256"] as? String == review["response"]
                && record["operator_confirmed"] as? Bool == true
        }
        if intent.kind == "memory_attach", let selection = job["memory_selection"] as? [String: Any] {
            return selection["selection_id"] as? String == review["mutation"]
                && selection["snapshot_sha256"] as? String == intent.fingerprint
                && selection["expected_revision"] as? Int == Int(review["expected"] ?? "")
                && selection["work_id"] as? String == intent.work
                && (selection["snapshot"] as? [String: Any])?["project_id"] as? String == intent.project
        }
        if intent.kind == "clarification_review", let questions = job["clarifications"] as? [[String: Any]],
           let question = questions.first(where: { $0["id"] as? String == review["question"] }),
           let history = question["history"] as? [[String: Any]] {
            return (job["candidate_import"] as? [String: Any]).flatMap { $0["origin"] as? [String: Any] }?["work_id"] as? String == intent.work
                && history.contains { item in
                    guard let reason = item["reason"] as? String else { return false }
                    return item["resolution_id"] as? String == review["mutation"]
                        && item["expected_revision"] as? Int == Int(review["expected"] ?? "")
                        && item["action"] as? String == review["action"]
                        && SHA256.hash(data: Data(reason.utf8)).map { String(format: "%02x", $0) }.joined() == review["reason"]
                }
        }
        if intent.kind == "clarify", let questions = job["clarifications"] as? [[String: Any]],
           let question = questions.first(where: { $0["id"] as? String == review["question"] }),
           let history = question["history"] as? [[String: Any]] {
            return (job["candidate_import"] as? [String: Any]).flatMap { $0["origin"] as? [String: Any] }?["work_id"] as? String == intent.work
                && history.contains { item in
                    guard let answer = item["answer"] as? String else { return false }
                    return item["resolution_id"] as? String == review["mutation"]
                        && item["expected_revision"] as? Int == Int(review["expected"] ?? "")
                        && item["resolved"] as? Bool == (review["resolved"] == "true")
                        && SHA256.hash(data: Data(answer.utf8)).map { String(format: "%02x", $0) }.joined() == review["answer"]
                }
        }
        return false
    }

    private func authorizationBackend(_ job: [String: Any]) -> String? {
        (job["operator_authorization"] as? [String: Any])?["backend"] as? String
    }

    private func clearConsentContext() {
        consentContext = [:]
        localPlan = [:]
        consentContextSHA256 = ""
    }

    var canConsent: Bool {
        enabled && !preparationBusy && !preparationUncertain && !preparationJournalUnavailable
            && preparationWorkMatches && (!isAnalysis || consentBackend == "foundation-models")
            && !clarifications.contains(where: { $0["resolved"] as? Bool != true })
            && preparationJob["status"] as? String == "preparation"
            && consentContext["project_id"] as? String == projectID
            && consentContext["work_id"] as? String == workID
            && consentContextSHA256.count == 64
            && (preparationJob["proposal"] as? [String: Any])?["questions"] as? [String] == []
            && (preparationJob["proposal"] as? [String: Any]).flatMap(canonicalProposalData) == canonicalProposalData(proposalPayload())
    }

    func loadConsentContext() {
        guard enabled, canonical(projectID), canonical(workID), !preparationBusy,
              !preparationUncertain, startHost() else { return }
        clearConsentContext()
        preparationBusy = true
        preparationStatus = "Validation locale du projet et du travail sélectionnés…"
        let id = request("job_consent_context", fields: ["enabled": true, "project_id": projectID, "work_id": workID])
        consentSelections[id] = [projectID, workID]
    }

    func authorizePreparation() {
        guard canConsent, let revision = preparationJob["revision"] as? Int,
              let digest = preparationJob["proposal_sha256"] as? String, startHost() else { return }
        preparationBusy = true
        preparationStatus = "Enregistrement du consentement opérateur ; aucune exécution…"
        request(consentBackend == "foundation-models" ? "job_authorize_local" : "job_authorize", fields: ["enabled": true, "project_id": projectID, "work_id": workID,
            "operation_id": preparationOperationID, "authorization_id": UUID().uuidString.lowercased(),
            "expected_revision": revision, "expected_proposal_sha256": digest,
            "expected_context_sha256": consentContextSHA256])
    }

    var canStartLocal: Bool {
        enabled && !preparationBusy && !preparationUncertain && !preparationJournalUnavailable
            && preparationJob["status"] as? String == "consented"
            && (preparationJob["operator_authorization"] as? [String: Any])?["backend"] as? String == "foundation-models"
            && (localPlan["manifest"] as? [String: Any])?["request_key"] as? String == preparationJob["request_key"] as? String
            && localPlan["manifest_sha256"] as? String != nil
    }

    func planLocalExecution() {
        guard enabled, !preparationBusy, !preparationUncertain,
              (preparationJob["operator_authorization"] as? [String: Any])?["backend"] as? String == "foundation-models",
              preparationJob["status"] as? String == "consented", startHost() else { return }
        preparationBusy = true
        let id = request("job_local_plan", fields: ["enabled": true, "project_id": projectID,
            "operation_id": preparationOperationID, "max_seconds": localMaxSeconds])
        localSelections[id] = [projectID, preparationOperationID]
    }

    func startLocalExecution() {
        guard canStartLocal, let revision = preparationJob["revision"] as? Int,
              let digest = localPlan["manifest_sha256"] as? String, startHost() else { return }
        preparationBusy = true
        request("job_local_start", fields: ["enabled": true, "project_id": projectID,
            "operation_id": preparationOperationID, "run_id": UUID().uuidString.lowercased(),
            "expected_revision": revision, "expected_manifest_sha256": digest])
    }

    func stopLocalExecution() {
        guard enabled, !preparationBusy,
              let execution = preparationJob["local_execution"] as? [String: Any],
              let run = execution["run_id"] as? String, startHost() else { return }
        preparationBusy = true
        request("job_local_stop", fields: ["enabled": true, "project_id": projectID,
            "operation_id": preparationOperationID, "run_id": run])
    }

    private func scheduleLocalStatus() {
        guard ["local_pending", "local_admitted", "local_running"].contains(preparationJob["status"] as? String ?? "") else { return }
        let project = projectID, operation = preparationOperationID
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.5) { [weak self] in
            guard let self, self.enabled, !self.preparationBusy, !self.preparationUncertain,
                  self.projectID == project, self.preparationOperationID == operation else { return }
            self.loadPreparation()
        }
    }

    func listCheckpoints() {
        guard enabled && canSelect && canonical(projectID) && canonical(workID), startHost() else { return }
        busy = true
        status = "Lecture des checkpoints…"
        request("list_checkpoints", fields: ["enabled": true, "project_id": projectID, "work_id": workID])
    }

    func open() {
        guard canOpen, startHost() else { return }
        busy = true
        status = "Ouverture du contexte sélectionné…"
        request("open", fields: ["enabled": true, "project_id": projectID, "work_id": workID,
                                 "checkpoint_id": checkpointID.isEmpty ? NSNull() : checkpointID as Any,
                                 "skill": skill])
    }

    func send() {
        guard canSend else { return }
        let text = prompt
        prompt = ""
        messages.append(Message(role: "Vous", text: text))
        busy = true
        status = "Réponse locale en cours…"
        turnID = request("turn", fields: ["text": text])
    }

    func cancel() {
        guard sessionOpen && busy && !closing && !cancelling, let turn = turnID else { return }
        cancelling = true
        status = "Annulation demandée ; attente de confirmation…"
        let ident = request("cancel")
        cancelTurnIDs[ident] = turn
    }

    func close() {
        guard process != nil, !closing, !memoryBusy, !preparationBusy else { return }
        closing = true
        sessionOpen = false
        status = "Fermeture demandée ; nettoyage en cours…"
        let current = generation
        DispatchQueue.main.asyncAfter(deadline: .now() + requestTimeout) { [weak self] in
            guard let self, self.generation == current, self.closing, self.process != nil else { return }
            self.ioFailed(generation: current)
        }
        request("close")
    }

    @discardableResult
    private func request(_ operation: String, fields: [String: Any] = [:]) -> String {
        let ident = UUID().uuidString.lowercased()
        var payload = fields
        payload["id"] = ident
        payload["op"] = operation
        guard let handle = input, JSONSerialization.isValidJSONObject(payload),
              var bytes = try? JSONSerialization.data(withJSONObject: payload), bytes.count < 65536 else {
            status = "Requête invalide ou trop volumineuse."
            busy = false
            if ["job_prepare", "job_recover", "job_revise", "job_authorize", "job_authorize_local", "job_consent_context", "job_memory_preview", "job_local_plan", "job_local_start", "job_local_stop", "job_candidate_import", "job_clarify", "job_clarification_review", "job_memory_attach"].contains(operation) {
                preparationBusy = false
                preparationStatus = "La requête locale n’a pas été envoyée ; conservez la même identité."
            }
            if operation == "memory_call" || operation == "memory_bind" {
                memoryBusy = false
                memoryStatus = "Requête mémoire non envoyée."
                if let id = mutationPendingID {
                    memoryOperationState = "Non envoyée : \(id)."
                    mutationPendingID = nil
                }
            }
            return ident
        }
        bytes.append(10)
        if ["job_prepare", "job_recover", "job_revise", "job_authorize", "job_authorize_local", "job_local_start", "job_local_stop", "job_candidate_import", "job_clarify", "job_clarification_review", "job_memory_attach"].contains(operation),
           let project = fields["project_id"] as? String, let preparation = fields["operation_id"] as? String {
            var intent: PreparationIntent?
            do {
                if operation == "job_candidate_import" {
                    guard let hash = fields["expected_candidate_sha256"] as? String,
                          let work = fields["work_id"] as? String,
                          let source = fields["source_operation_id"] as? String,
                          let run = fields["source_run_id"] as? String,
                          let response = fields["expected_response_sha256"] as? String,
                          let mutation = fields["import_id"] as? String else { throw PreparationIntentJournal.JournalError.writeFailed }
                    let record = PreparationIntent(project: project, operation: preparation, kind: "candidate_import",
                        revision: 0, fingerprint: hash, attempt: UUID().uuidString.lowercased(), work: work,
                        review: ["source": source, "run": run, "response": response, "mutation": mutation])
                    try preparationJournal.write(record); intent = record
                    importRefusalAvailable = false
                    guard saveAnalysisLink(PreparationLink(project: project, work: work, analysis: source, mission: preparation)) else {
                        throw PreparationIntentJournal.JournalError.writeFailed
                    }
                } else if operation == "job_memory_attach" {
                    guard let revision = fields["expected_revision"] as? Int,
                          let hash = fields["snapshot_sha256"] as? String,
                          let work = fields["work_id"] as? String,
                          let mutation = fields["selection_id"] as? String else { throw PreparationIntentJournal.JournalError.writeFailed }
                    let record = PreparationIntent(project: project, operation: preparation, kind: "memory_attach",
                        revision: revision + 1, fingerprint: hash, attempt: UUID().uuidString.lowercased(), work: work,
                        review: ["mutation": mutation, "snapshot": hash, "expected": String(revision)])
                    try preparationJournal.write(record); intent = record
                } else if operation == "job_clarification_review" {
                    guard let revision = fields["expected_revision"] as? Int,
                          let question = fields["question_id"] as? String,
                          let reason = fields["reason"] as? String,
                          let action = fields["action"] as? String,
                          let mutation = fields["resolution_id"] as? String else { throw PreparationIntentJournal.JournalError.writeFailed }
                    let hash = SHA256.hash(data: Data(reason.utf8)).map { String(format: "%02x", $0) }.joined()
                    let record = PreparationIntent(project: project, operation: preparation, kind: "clarification_review",
                        revision: revision + 1, fingerprint: hash, attempt: UUID().uuidString.lowercased(), work: workID,
                        review: ["question": question, "reason": hash, "action": action,
                                 "expected": String(revision), "mutation": mutation])
                    try preparationJournal.write(record); intent = record
                } else if operation == "job_clarify" {
                    guard let revision = fields["expected_revision"] as? Int,
                          let question = fields["question_id"] as? String,
                          let answer = fields["answer"] as? String,
                          let resolved = fields["resolved"] as? Bool,
                          let mutation = fields["resolution_id"] as? String else { throw PreparationIntentJournal.JournalError.writeFailed }
                    let hash = SHA256.hash(data: Data(answer.utf8)).map { String(format: "%02x", $0) }.joined()
                    let record = PreparationIntent(project: project, operation: preparation, kind: "clarify",
                        revision: revision + 1, fingerprint: hash, attempt: UUID().uuidString.lowercased(), work: workID,
                        review: ["question": question, "answer": hash, "resolved": resolved ? "true" : "false",
                                 "expected": String(revision), "mutation": mutation])
                    try preparationJournal.write(record); intent = record
                } else if operation == "job_local_start" {
                    guard let run = fields["run_id"] as? String,
                          let expected = fields["expected_revision"] as? Int,
                          let fingerprint = fields["expected_manifest_sha256"] as? String,
                          let manifest = localPlan["manifest"] as? [String: Any],
                          let context = manifest["context_sha256"] as? String,
                          let work = manifest["work_id"] as? String else { throw PreparationIntentJournal.JournalError.writeFailed }
                    let record = PreparationIntent(project: project, operation: preparation, kind: "local_start",
                        revision: expected + 1, fingerprint: fingerprint, attempt: UUID().uuidString.lowercased(),
                        authorizationID: run, contextSHA256: context, work: work)
                    try preparationJournal.write(record); intent = record
                } else if operation == "job_authorize" || operation == "job_authorize_local" {
                    guard let authorization = fields["authorization_id"] as? String,
                          let work = fields["work_id"] as? String,
                          let context = fields["expected_context_sha256"] as? String,
                          let fingerprint = fields["expected_proposal_sha256"] as? String,
                          let expected = fields["expected_revision"] as? Int else { throw PreparationIntentJournal.JournalError.writeFailed }
                    let record = PreparationIntent(project: project, operation: preparation, kind: operation == "job_authorize_local" ? "authorize_local" : "authorize",
                        revision: expected + 1, fingerprint: fingerprint, attempt: UUID().uuidString.lowercased(),
                        authorizationID: authorization, contextSHA256: context, work: work)
                    try preparationJournal.write(record)
                    intent = record
                } else if operation == "job_prepare" || operation == "job_revise" {
                    guard let proposal = fields["proposal"] as? [String: Any],
                          let fingerprint = proposalFingerprint(proposal) else { throw PreparationIntentJournal.JournalError.writeFailed }
                    let revision: Int
                    let kind: String
                    if operation == "job_prepare" { kind = "prepare"; revision = 0 }
                    else {
                        guard let expected = fields["expected_revision"] as? Int, expected >= 0 else {
                            throw PreparationIntentJournal.JournalError.writeFailed
                        }
                        kind = "revise"; revision = expected + 1
                    }
                    let record = PreparationIntent(project: project, operation: preparation,
                        kind: kind, revision: revision, fingerprint: fingerprint, attempt: UUID().uuidString.lowercased())
                    try preparationJournal.write(record)
                    intent = record
                } else if let existing = try preparationJournal.read(project: project) {
                    guard existing.operation == preparation else { throw PreparationIntentJournal.JournalError.identityConflict }
                    intent = existing
                }
                preparationJournalUnavailable = false
            } catch {
                preparationBusy = false
                preparationUncertain = true
                preparationJournalUnavailable = true
                preparationStatus = "Journal local non durable ; aucune requête n’a été envoyée."
                return ident
            }
            preparationPending[ident] = PendingPreparation(project: project, operation: preparation, intent: intent, selection: operationSelection)
        }
        pending[ident] = operation
        let current = generation
        DispatchQueue.main.asyncAfter(deadline: .now() + requestTimeout) { [weak self] in
            guard let self, self.generation == current, self.pending[ident] != nil else { return }
            if self.pending[ident] == "cancel", self.cancelTurnIDs[ident] != self.turnID { return }
            self.ioFailed(generation: current)
        }
        ioQueue.async { [weak self] in
            do { try handle.write(contentsOf: bytes) }
            catch {
                Task { @MainActor [weak self] in self?.ioFailed(generation: current) }
            }
        }
        return ident
    }

    private func readOutput(_ handle: FileHandle, generation current: UUID) {
        readQueue.async { [weak self] in
            var buffer = Data()
            while true {
                let data = handle.availableData
                if data.isEmpty {
                    let invalid = !buffer.isEmpty
                    Task { @MainActor [weak self] in self?.outputEnded(generation: current, invalid: invalid) }
                    return
                }
                buffer.append(data)
                while let newline = buffer.firstIndex(of: 10) {
                    let frame = Data(buffer[..<newline])
                    buffer.removeSubrange(...newline)
                    guard frame.count <= Self.maximumFrame else {
                        Task { @MainActor [weak self] in self?.ioFailed(generation: current) }
                        return
                    }
                    Task { @MainActor [weak self] in self?.received(frame, generation: current) }
                }
                if buffer.count > Self.maximumFrame {
                    Task { @MainActor [weak self] in self?.ioFailed(generation: current) }
                    return
                }
            }
        }
    }

    private func received(_ data: Data, generation current: UUID) {
        guard current == generation,
              let object = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any],
              let ident = object["id"] as? String, let operation = pending.removeValue(forKey: ident),
              let result = object["status"] as? String else {
            ioFailed(generation: current); return
        }
        if result == "cleanup_unconfirmed" {
            cleanupUnconfirmed = true
            ioFailed(generation: current)
            return
        }
        switch operation {
        case "job_memory_preview":
            preparationBusy = false
            guard let selection = recallPreviewSelection.removeValue(forKey: ident),
                  selection.0 == projectID, selection.1 == workID, selection.2 == preparationOperationID,
                  selection.3 == operationSelection, selection.4 == recallSelected else { return }
            if result == "finished", let preview = object["memory_preview"] as? [String: Any],
               preview["project_id"] as? String == projectID, preview["work_id"] as? String == workID,
               preview["schema"] as? String == "reprise-memory-selection-v1",
               let facts = preview["facts"] as? [[String: Any]],
               Set(facts.compactMap { $0["id"] as? String }) == recallSelected,
               let hash = object["snapshot_sha256"] as? String, hash.count == 64 {
                recallPreview = preview; recallPreviewSHA256 = hash; recallAcknowledged = false
                preparationStatus = "Aperçu prêt ; relisez les copies et leurs sources avant approbation."
            } else {
                clearRecallPreview()
                preparationStatus = "Aperçu mémoire indisponible (\(result)) ; rien n’a été joint."
            }
        case "job_list":
            guard let selection = browserRequests.removeValue(forKey: ident),
                  selection.0 == projectID, selection.1 == workID, selection.2 == operationSelection else { return }
            operationBrowserBusy = false
            guard result == "finished", let page = object["page"] as? [String: Any],
                  page["project_id"] as? String == projectID, page["offset"] as? Int == selection.3,
                  page["limit"] as? Int == 20,
                  page["work_id"] as? String == (operationFilterWork ? workID : nil),
                  let rows = page["operations"] as? [[String: Any]], rows.count <= 20,
                  rows.allSatisfy({ $0["project_id"] as? String == projectID }) else {
                operationRows = []; selectedExistingOperation = ""
                operationBrowserStatus = "Liste indisponible (\(result)). Aucune opération lancée."
                return
            }
            operationRows = rows; operationOffset = selection.3
            operationNextOffset = page["next_offset"] as? Int; selectedExistingOperation = ""
            operationBrowserStatus = rows.isEmpty ? "Aucune opération dans ce périmètre." : "Choisissez une opération ; ouvrir ne relance rien."
        case "job_candidate_extract":
            preparationBusy = false
            guard let selection = candidateSelections.removeValue(forKey: ident),
                  Array(selection.prefix(3)) == [projectID, workID, preparationOperationID] else { return }
            if result == "finished", let value = object["candidate"] as? [String: Any],
               let origin = value["origin"] as? [String: Any],
               origin["project_id"] as? String == projectID, origin["work_id"] as? String == workID,
               origin["source_operation_id"] as? String == preparationOperationID,
               origin["source_run_id"] as? String == selection[3], origin["response_sha256"] as? String == selection[4],
               let hash = value["candidate_sha256"] as? String, hash.count == 64 {
                candidateResult = value
                preparationStatus = "Candidat extrait du reçu ; relisez-le avant import explicite. Aucune mission autorisée."
            } else {
                candidateResult = [:]
                preparationStatus = "Candidat indisponible (\(result)). Réponse brute conservée ; aucune nouvelle génération."
            }
        case "job_local_plan":
            preparationBusy = false
            guard let selection = localSelections.removeValue(forKey: ident), selection == [projectID, preparationOperationID] else { return }
            if result == "finished", let plan = object["local_plan"] as? [String: Any],
               let manifest = plan["manifest"] as? [String: Any], manifest["backend"] as? String == "foundation-models",
               manifest["project_id"] as? String == projectID, manifest["work_id"] as? String == workID,
               manifest["request_key"] as? String == preparationJob["request_key"] as? String {
                localPlan = plan
                preparationStatus = "Exécutable local identifié ; disponibilité de génération non vérifiée. Relisez le manifeste avant exécution."
            } else {
                localPlan = [:]
                preparationStatus = "Plan local indisponible (\(result)) ; aucune inférence lancée."
            }
        case "job_consent_context":
            preparationBusy = false
            guard let selection = consentSelections.removeValue(forKey: ident), selection == [projectID, workID] else { return }
            if result == "finished", let context = object["context"] as? [String: Any],
               context["project_id"] as? String == projectID, context["work_id"] as? String == workID,
               context["execution_verification"] as? String == "not_performed",
               let digest = object["context_sha256"] as? String, digest.count == 64 {
                consentContext = context; consentContextSHA256 = digest
                preparationStatus = "Contexte local validé ; disponibilité et facturation non vérifiées."
            } else {
                clearConsentContext()
                preparationStatus = "Contexte local indisponible (\(result)) ; consentement bloqué."
            }
        case "job_prepare", "job_recover", "job_revise", "job_authorize", "job_authorize_local", "job_local_start", "job_local_stop", "job_candidate_import", "job_clarify", "job_clarification_review", "job_memory_attach":
            guard let context = preparationPending.removeValue(forKey: ident) else { ioFailed(generation: current); return }
            preparationBusy = false
            guard context.project == projectID, context.project == preparationProjectID,
                  context.operation == preparationOperationID, context.selection == operationSelection else { return }
            if result == "finished", let job = object["job"] as? [String: Any],
               job["project_id"] as? String == context.project,
               job["operation_id"] as? String == context.operation {
                preparationJob = job
                if let intent = context.intent {
                    let authorization = job["operator_authorization"] as? [String: Any]
                    let authorizationProof = ["authorize", "authorize_local"].contains(intent.kind)
                        && (intent.kind != "authorize_local" || authorization?["backend"] as? String == "foundation-models")
                        && job["status"] as? String == "consented"
                        && job["authorization_kind"] as? String == "operator_consent_v1"
                        && authorization?["execution_authorized"] as? Bool == false
                        && job["revision"] as? Int == intent.revision
                        && job["proposal_sha256"] as? String == intent.fingerprint
                        && authorization?["authorization_id"] as? String == intent.authorizationID
                        && authorization?["context_sha256"] as? String == intent.contextSHA256
                        && (authorization?["host_context"] as? [String: Any])?["work_id"] as? String == intent.work
                    let execution = job["local_execution"] as? [String: Any]
                    let manifest = execution?["manifest"] as? [String: Any]
                    let localProof = intent.kind == "local_start"
                        && execution?["run_id"] as? String == intent.authorizationID
                        && execution?["manifest_sha256"] as? String == intent.fingerprint
                        && manifest?["context_sha256"] as? String == intent.contextSHA256
                        && manifest?["work_id"] as? String == intent.work
                        && manifest?["backend"] as? String == "foundation-models"
                        && (job["revision"] as? Int ?? -1) >= intent.revision
                    let exactProof = authorizationProof || localProof || reviewProof(intent, job: job) || (!["authorize", "authorize_local", "local_start", "candidate_import", "clarify", "clarification_review", "memory_attach"].contains(intent.kind) && job["status"] as? String == "preparation"
                        && job["revision"] as? Int == intent.revision
                        && (job["proposal"] as? [String: Any]).flatMap(proposalFingerprint) == intent.fingerprint)
                    if exactProof {
                        applyProposalToEditor(job)
                        let markerCleared = clearPreparationIntent(intent, effect: "finished", status: result)
                        if markerCleared { setPreparationUncertain(false, project: context.project, operation: context.operation) }
                        preparationStatus = markerCleared
                            ? "Préparation \(job["status"] as? String ?? "chargée") · révision \(job["revision"] as? Int ?? 0) · preuve de mutation retrouvée."
                            : "Mutation retrouvée ; journal non effacé durablement, récupérez avant toute mutation."
                    } else {
                        setPreparationUncertain(true, project: context.project, operation: context.operation)
                        preparationStatus = "État antérieur ou différent retrouvé ; l’intention de révision reste incertaine, récupérez à nouveau."
                    }
                } else {
                    applyProposalToEditor(job)
                    preparationStatus = "Préparation \(job["status"] as? String ?? "chargée") · révision \(job["revision"] as? Int ?? 0) · sans autorité ni dispatch."
                }
                if job["status"] as? String == "consented", !preparationUncertain {
                    preparationStatus = authorizationBackend(job) == "foundation-models"
                        ? "Consentement Apple local enregistré ; aucune exécution avant validation du manifeste."
                        : "Consentement enregistré ; exécution pas encore disponible. Facturation non vérifiée."
                }
                if let execution = job["local_execution"] as? [String: Any], !preparationUncertain {
                    let receipt = execution["receipt"] as? [String: Any]
                    preparationStatus = "Exécution locale : \(execution["stage"] as? String ?? "inconnue") · \(receipt?["status"] as? String ?? "résultat non capturé")"
                    scheduleLocalStatus()
                }
            } else if result == "not_found" {
                preparationJob = [:]
                // Absence is not a fence: an older worker may still be suspended
                // before its store write and create this operation later.
                if context.intent != nil {
                    setPreparationUncertain(true, project: context.project, operation: context.operation)
                    preparationStatus = "Aucune préparation visible pour l’instant ; une mutation ancienne peut encore s’appliquer. Identité conservée, récupérez à nouveau avant toute mutation."
                } else {
                    preparationStatus = "Aucune préparation enregistrée pour cette identité."
                }
            } else if operation != "job_recover", object["effect"] as? String == "not_admitted", let intent = context.intent {
                let cleared = clearPreparationIntent(intent, effect: "not_admitted", status: result)
                if cleared { setPreparationUncertain(false, project: context.project, operation: context.operation) }
                preparationStatus = cleared ? "Préparation refusée (\(result)) ; corrigez la proposition puis réessayez." : "Refus confirmé ; journal non effacé durablement, récupérez avant toute mutation."
            } else if result == "write_uncertain" {
                setPreparationUncertain(true, project: context.project, operation: context.operation)
                preparationStatus = "Écriture incertaine après remplacement ; rechargez la même opération avant toute mutation."
            } else {
                if context.intent != nil {
                    // Errors from recover do not fence the old writer or prove
                    // which state version it may still commit.
                    setPreparationUncertain(true, project: context.project, operation: context.operation)
                } else {
                    preparationStatus = "Préparation : \(result)."
                }
                if context.intent != nil {
                    preparationStatus = "Récupération non concluante (\(result)) ; journal conservé, réessayez la récupération."
                }
            }
        case "open":
            busy = false
            sessionOpen = result == "opened"
            status = sessionOpen ? "Pilote local prêt ; le contexte reste expérimental." : "Ouverture refusée ou contexte indisponible."
            if !sessionOpen { finishInput() }
        case "list_checkpoints":
            busy = false
            checkpoints = result == "finished" ? (object["checkpoints"] as? [[String: Any]] ?? []) : []
            status = result == "finished" ? "Checkpoints chargés : choisissez-en un si nécessaire." : "Lecture des checkpoints impossible."
        case "memory_bind":
            memoryBusy = false
            if result == "bound" {
                memoryProject = bindTarget
                memoryStatus = "Mémoire liée au projet choisi."
            } else {
                memoryProject = ""
                memoryStatus = "Liaison impossible : \(result)."
            }
        case "memory_call":
            receiveMemory(object, ident: ident, result: result)
        case "turn":
            busy = false
            cancelling = false
            turnID = nil
            if result == "finished", let answer = object["response"] as? String {
                messages.append(Message(role: "Apple local", text: answer))
                status = "Réponse reçue ; mémoire et effets externes non vérifiés."
            } else if result == "cancelled" {
                status = "Tour annulé."
            } else {
                status = "Tour non terminé : \(result)."
            }
            if closing { finishInput() }
        case "cancel":
            let cancelledTurn = cancelTurnIDs.removeValue(forKey: ident)
            if result == "cancelling", busy, cancelling, let turnID, cancelledTurn == turnID {
                status = "Annulation demandée ; attente de confirmation…"
            }
        case "close":
            if result == "closed" { finishInput() }
            else if result == "cancelling" { status = "Fermeture demandée ; attente du tour en cours…" }
            else { ioFailed(generation: current) }
        default:
            ioFailed(generation: current)
        }
    }

    private func applyProposalToEditor(_ job: [String: Any]) {
        guard let proposal = job["proposal"] as? [String: Any] else { return }
        proposalObjective = proposal["objective"] as? String ?? ""
        proposalAcceptance = (proposal["acceptance"] as? [String] ?? []).joined(separator: "\n")
        proposalConstraints = (proposal["constraints"] as? [String] ?? []).joined(separator: "\n")
        proposalQuestions = (proposal["questions"] as? [String] ?? []).joined(separator: "\n")
    }

    var memoryReady: Bool {
        enabled && canonical(projectID) && !memoryBusy && !closing && !hostTerminating && !operationBrowserBusy
            && (!cleanupUnconfirmed || process == nil || memoryRecoveryHost)
    }

    func bindMemory() {
        guard memoryReady, startHost() else { return }
        memoryBusy = true
        bindTarget = projectID
        memoryStatus = "Liaison de la mémoire…"
        request("memory_bind", fields: ["enabled": true, "project_id": projectID])
    }

    private func memoryCall(_ kind: String, fields: [String: Any] = [:], mutation: Bool = false) {
        guard memoryReady, memoryProject == projectID else {
            memoryStatus = "Liez d’abord la mémoire au projet sélectionné."
            return
        }
        var body = fields
        body["op"] = kind
        if mutation {
            guard !memoryUncertain && !cleanupUnconfirmed else { memoryStatus = "Vérifiez d’abord les états incertains."; return }
            let operation = UUID().uuidString.lowercased()
            body["operation_id"] = operation
            memoryOperationID = operation
            memoryOperationProject = projectID
            memoryInspectOperationID = operation
            mutationPendingID = operation
            memoryOperationState = "Opération envoyée ; résultat en attente."
        }
        memoryBusy = true
        memoryStatus = "Opération mémoire en cours…"
        let ident = request("memory_call", fields: ["request": body])
        if pending[ident] != nil {
            memoryPending[ident] = kind
            if ["search_facts", "get_fact", "list_facts"].contains(kind) {
                memoryReadSelections[ident] = (projectID, workID, operationSelection)
            }
            if kind == "operation_status", let id = fields["operation_id"] as? String { memoryCheckIDs[ident] = id }
        }
    }

    func loadFacts(offset: Int = 0) { memoryCall("list_facts", fields: ["offset": offset]) }
    func loadFact(_ id: String) { memoryCall("get_fact", fields: ["fact_id": id]) }
    func loadMemoryCheckpoints(offset: Int = 0) {
        guard canonical(workID) else { memoryStatus = "Choisissez l’UUID du travail."; return }
        memoryCall("list_checkpoints", fields: ["work_id": workID, "offset": offset])
    }
    func loadCheckpoint(_ id: String) {
        guard canonical(workID) else { return }
        memoryCall("get_checkpoint", fields: ["work_id": workID, "checkpoint_id": id])
    }
    func loadIntents(offset: Int = 0) { memoryCall("list_intents", fields: ["offset": offset]) }
    func inspectOperation() {
        guard canonical(memoryInspectOperationID) else { memoryStatus = "Identifiant d’opération invalide."; return }
        memoryCall("operation_status", fields: ["operation_id": memoryInspectOperationID])
    }
    private func adoptDraft() {
        guard !resettingDraft, draftProjectID.isEmpty else { return }
        draftProjectID = canonical(projectID) ? projectID : "unassigned"
    }

    private func adoptCheckpointDraft() {
        guard !resettingDraft else { return }
        adoptDraft()
        if checkpointDraftWorkID.isEmpty {
            checkpointDraftWorkID = canonical(workID) ? workID : "unassigned"
        }
    }

    var draftEditable: Bool {
        canonical(projectID) && (draftProjectID.isEmpty || draftProjectID == projectID)
            && (checkpointDraftWorkID.isEmpty || checkpointDraftWorkID == workID)
    }
    var checkpointEditable: Bool { draftEditable && canonical(workID) }
    var draftMatchesProject: Bool { !draftProjectID.isEmpty && draftProjectID == projectID }
    var checkpointDraftMatchesWork: Bool { !checkpointDraftWorkID.isEmpty && checkpointDraftWorkID == workID }

    func discardMemoryDraft() {
        guard !memoryBusy else { return }
        resettingDraft = true
        factText = ""; sourceKind = ""; sourceReference = ""; sourceProducer = ""
        sourceObservedAt = ""; sourceSHA256 = ""; selectedFactIDs = []; checkpointVersion = "0"
        decisions = ""; constraints = ""; uncertainEffects = ""; nextAction = ""
        draftProjectID = ""
        checkpointDraftWorkID = ""
        resettingDraft = false
    }

    private func source() -> [String: String]? {
        let fields = ["kind": sourceKind, "reference": sourceReference, "producer": sourceProducer, "observed_at": sourceObservedAt]
        guard fields.values.allSatisfy({ !$0.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty && $0.utf8.count <= 512 }) else {
            memoryStatus = "Complétez la provenance déclarée."
            return nil
        }
        var result = fields
        if !sourceSHA256.isEmpty { result["sha256"] = sourceSHA256 }
        return result
    }
    func createFact() {
        guard draftEditable else { memoryStatus = "Le contexte du brouillon a changé ; revenez à son projet et travail."; return }
        guard draftMatchesProject else { memoryStatus = "Brouillon lié à un autre projet ; retournez-y ou effacez-le explicitement."; return }
        guard let source = source(), !factText.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else { return }
        memoryCall("create_fact", fields: ["text": factText, "source": source], mutation: true)
    }
    func createCheckpoint() {
        guard checkpointDraftMatchesWork else { memoryStatus = "Brouillon lié à un autre travail ; retournez-y ou effacez-le explicitement."; return }
        guard draftMatchesProject else { memoryStatus = "Brouillon lié à un autre projet ; retournez-y ou effacez-le explicitement."; return }
        guard canonical(workID), let version = Int(checkpointVersion), version >= 0,
              let source = source(), !nextAction.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
            memoryStatus = "Vérifiez travail, version, prochaine action et provenance."
            return
        }
        func lines(_ text: String) -> [String] { text.split(separator: "\n").map(String.init) }
        memoryCall("create_checkpoint", fields: ["work_id": workID, "expected_version": version,
            "fact_ids": selectedFactIDs.sorted(), "decisions": lines(decisions), "constraints": lines(constraints),
            "next_action": nextAction, "uncertain_effects": lines(uncertainEffects), "source": source], mutation: true)
    }

    private func receiveMemory(_ object: [String: Any], ident: String, result: String) {
        memoryBusy = false
        guard let kind = memoryPending.removeValue(forKey: ident) else { ioFailed(generation: generation); return }
        if let selection = memoryReadSelections.removeValue(forKey: ident),
           selection.0 != projectID || selection.1 != workID || selection.2 != operationSelection { return }
        if kind == "create_fact" || kind == "create_checkpoint" {
            let effect = object["effect"] as? String
            if result == "finished", effect == "recorded" {
                memoryOperationState = "Enregistrée : \(memoryOperationID)."
                memoryUncertain = false
            } else if effect == "not_admitted" {
                memoryOperationState = "Non admise : \(memoryOperationID)."
                memoryUncertain = false
            } else {
                memoryOperationState = "Résultat incertain : \(memoryOperationID). Vérifiez le reçu."
                memoryUncertain = true
            }
            mutationPendingID = nil
        } else if result == "finished" {
            switch kind {
            case "list_facts", "search_facts":
                facts = object["items"] as? [[String: Any]] ?? []
                factsNextOffset = object["next_offset"] as? Int
            case "get_fact": factDetail = object["item"] as? [String: Any] ?? [:]
            case "list_checkpoints":
                checkpoints = object["items"] as? [[String: Any]] ?? []
                checkpointsNextOffset = object["next_offset"] as? Int
            case "get_checkpoint": checkpointDetail = object["item"] as? [String: Any] ?? [:]
            case "list_intents":
                memoryIntents = object["items"] as? [[String: Any]] ?? []
                intentsNextOffset = object["next_offset"] as? Int
            case "operation_status":
                let state = object["state"] as? String ?? "inconnu"
                let intent = object["intent"] as? String ?? "inconnu"
                let inspected = memoryCheckIDs.removeValue(forKey: ident) ?? "inconnu"
                memoryOperationState = "Reçu \(inspected) : \(state), intention \(intent). Une absence ne prouve pas l’arrêt du travail."
                if state == "recorded", inspected == memoryOperationID, memoryProject == memoryOperationProject {
                    memoryUncertain = false
                }
            default: break
            }
        }
        memoryCheckIDs.removeValue(forKey: ident)
        memoryStatus = result == "finished" ? "Lecture ou opération terminée." : "Opération mémoire : \(result)."
    }

    private func finishInput() {
        guard let handle = input else { return }
        input = nil
        ioQueue.async { try? handle.close() }
    }

    private func ioFailed(generation current: UUID) {
        guard current == generation else { return }
        if turnID != nil || (busy && !memoryBusy) { cleanupUnconfirmed = true }
        if preparationBusy {
            preparationBusy = false
            if preparationPending.values.contains(where: { $0.intent != nil }) {
                setPreparationUncertain(true, project: preparationProjectID, operation: preparationOperationID)
                preparationStatus = "Accusé perdu ; mutation incertaine, récupérez la même identité avant toute correction."
            } else {
                preparationStatus = "Lecture interrompue ; aucun effet de mutation n’était en attente."
            }
        }
        hostTerminating = process != nil
        if let operation = mutationPendingID {
            memoryOperationID = operation
            memoryOperationState = "Résultat incertain ; conserver l’identifiant et vérifier le reçu."
            memoryUncertain = true
        }
        operationBrowserBusy = false; browserRequests.removeAll()
        recallPreviewSelection.removeAll(); clearRecallPreview()
        operationBrowserStatus = "Lecture interrompue ; rechargez explicitement la liste."
        memoryBusy = false
        mutationPendingID = nil
        memoryPending.removeAll(); memoryReadSelections.removeAll()
        memoryCheckIDs.removeAll()
        sessionOpen = false
        busy = false
        closing = false
        cancelling = false
        pending.removeAll()
        preparationPending.removeAll()
        cancelTurnIDs.removeAll()
        status = cleanupUnconfirmed ? "Communication interrompue ; nettoyage non confirmé." : "Communication avec le pilote impossible."
        finishInput()
    }

    private func outputEnded(generation current: UUID, invalid: Bool) {
        guard current == generation else { return }
        if invalid || input != nil { ioFailed(generation: current) }
        readEnded = true
        finalizeIfEnded()
    }

    private func terminated(generation current: UUID, code: Int32) {
        guard current == generation else { return }
        exitCode = code
        finalizeIfEnded()
    }

    private func finalizeIfEnded() {
        guard readEnded, let code = exitCode else { return }
        if code != 0 && turnID != nil { cleanupUnconfirmed = true }
        process = nil
        hostTerminating = false
        memoryRecoveryHost = false
        turnID = nil
        input = nil
        pending.removeAll()
        preparationPending.removeAll()
        cancelTurnIDs.removeAll()
        memoryPending.removeAll(); memoryReadSelections.removeAll()
        memoryCheckIDs.removeAll()
        memoryProject = ""
        memoryBusy = false
        messages.removeAll()
        sessionOpen = false
        busy = false
        closing = false
        cancelling = false
        status = cleanupUnconfirmed ? "Processus arrêté ; nettoyage non confirmé." : "Pilote arrêté ; conversation effacée."
    }
}
