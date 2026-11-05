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

/// Crash-durable identity marker written before any preparation request is emitted.
private struct PreparationIntent: Codable, Equatable {
    let project: String
    let operation: String
    let kind: String
    let revision: Int
    let fingerprint: String
    let attempt: String?

    init(project: String, operation: String, kind: String, revision: Int, fingerprint: String, attempt: String? = nil) {
        self.project = project; self.operation = operation; self.kind = kind
        self.revision = revision; self.fingerprint = fingerprint; self.attempt = attempt
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

    func terminal(project: String) throws -> PreparationIntent? {
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
                    guard count >= 0, data.count + count <= 2048 else { throw JournalError.unsafeFile }
                    if count == 0 { break }
                    data.append(buffer, count: count)
                }
                let receipt = try JSONDecoder().decode(TerminalReceipt.self, from: data)
                let intent = receipt.intent
                guard intent.project == project,
                      intent.attempt == nil || UUID(uuidString: intent.attempt!)?.uuidString.lowercased() == intent.attempt,
                      UUID(uuidString: intent.operation)?.uuidString.lowercased() == intent.operation,
                      ["prepare", "revise"].contains(intent.kind), intent.revision >= 0,
                      intent.fingerprint.count == 64,
                      intent.fingerprint.allSatisfy({ $0.isHexDigit && !$0.isUppercase }),
                      ["finished", "not_admitted"].contains(receipt.effect), !receipt.status.isEmpty,
                      receipt.status.count <= 64,
                      receipt.status.allSatisfy({ $0.isASCII && ($0.isLetter || $0 == "_") }) else {
                    throw JournalError.unsafeFile
                }
                // Retry durability after a previous rename followed by failed fsync.
                guard syncFD(fd) == 0, syncFD(folder) == 0 else { throw JournalError.writeFailed }
                return intent
            }
        }
    }

    func write(_ intent: PreparationIntent) throws {
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
            guard count >= 0, data.count + count <= 1024 else { throw JournalError.unsafeFile }
            if count == 0 { break }
            data.append(buffer, count: count)
        }
        let object = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any]
        if let object, [Set(["project", "operation", "kind", "revision", "fingerprint"]), Set(["project", "operation", "kind", "revision", "fingerprint", "attempt"])].contains(Set(object.keys)),
           let intent = try? JSONDecoder().decode(PreparationIntent.self, from: data),
           intent.project == project,
           intent.attempt == nil || UUID(uuidString: intent.attempt!)?.uuidString.lowercased() == intent.attempt,
           UUID(uuidString: intent.operation)?.uuidString.lowercased() == intent.operation,
           ["prepare", "revise"].contains(intent.kind), intent.revision >= 0,
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
    }

    struct Message: Identifiable {
        let id = UUID()
        let role: String
        let text: String
    }

    @Published var enabled = false { didSet { preferences?.set(enabled, forKey: StartupPreferences.pilotEnabledKey) } }
    @Published var showMemory = false
    @Published var projectID = "" { didSet { saveSelection(projectID, key: "ApplePilotProject", uuid: true); selectPreparationProject(projectID) } }
    @Published var workID = "" { didSet { saveSelection(workID, key: "ApplePilotWork", uuid: true) } }
    @Published var checkpointID = "" { didSet { saveSelection(checkpointID, key: "ApplePilotCheckpoint", uuid: true) } }
    @Published var skill = "checkpoint-review" { didSet { saveSelection(skill, key: "ApplePilotSkill", uuid: false) } }
    @Published var prompt = ""
    @Published var proposalObjective = ""
    @Published var proposalAcceptance = ""
    @Published var proposalConstraints = ""
    @Published var proposalQuestions = ""
    @Published private(set) var preparationOperationID = ""
    @Published private(set) var preparationStatus = "Aucune préparation chargée."
    @Published private(set) var preparationJob: [String: Any] = [:]
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
    var canSelect: Bool { !sessionOpen && !busy && !memoryBusy && !preparationBusy && !closing && !cleanupUnconfirmed && !hostTerminating }
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
            if let terminal = try preparationJournal.terminal(project: project), active == nil || active == terminal {
                preparationOperationID = terminal.operation
                preparationProjectID = project
                guard preparationJournal.clear(terminal) else { throw PreparationIntentJournal.JournalError.writeFailed }
                setPreparationUncertain(false, project: project, operation: terminal.operation)
                preparationUncertain = false
                preparationJournalUnavailable = false
                return
            }
            if let intent = active {
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

    var preparationCanEdit: Bool { preparationJob["status"] as? String == "preparation" && !preparationBusy && !preparationUncertain && !preparationJournalUnavailable }

    func newPreparation() {
        guard enabled, canonical(projectID), !preparationBusy, !preparationUncertain,
              !preparationJournalUnavailable, !closing else { return }
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
            if operation == "job_prepare" || operation == "job_recover" || operation == "job_revise" {
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
        if ["job_prepare", "job_recover", "job_revise"].contains(operation),
           let project = fields["project_id"] as? String, let preparation = fields["operation_id"] as? String {
            var intent: PreparationIntent?
            do {
                if operation == "job_prepare" || operation == "job_revise" {
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
            preparationPending[ident] = PendingPreparation(project: project, operation: preparation, intent: intent)
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
        case "job_prepare", "job_recover", "job_revise":
            guard let context = preparationPending.removeValue(forKey: ident) else { ioFailed(generation: current); return }
            preparationBusy = false
            guard context.project == projectID, context.project == preparationProjectID,
                  context.operation == preparationOperationID else { return }
            if result == "finished", let job = object["job"] as? [String: Any],
               job["project_id"] as? String == context.project,
               job["operation_id"] as? String == context.operation {
                preparationJob = job
                if let intent = context.intent {
                    let exactProof = job["status"] as? String == "preparation"
                        && job["revision"] as? Int == intent.revision
                        && (job["proposal"] as? [String: Any]).flatMap(proposalFingerprint) == intent.fingerprint
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
        enabled && canonical(projectID) && !memoryBusy && !closing && !hostTerminating
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
            case "list_facts":
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
        memoryBusy = false
        mutationPendingID = nil
        memoryPending.removeAll()
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
        memoryPending.removeAll()
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
