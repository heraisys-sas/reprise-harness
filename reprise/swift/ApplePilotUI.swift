import Foundation
import Combine

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

/// Isolated UI controller. The caller supplies trusted bundled executable paths.
@MainActor
final class ApplePilotUI: ObservableObject {
    struct Message: Identifiable {
        let id = UUID()
        let role: String
        let text: String
    }

    @Published var enabled = false { didSet { preferences?.set(enabled, forKey: StartupPreferences.pilotEnabledKey) } }
    @Published var showMemory = false
    @Published var projectID = "" { didSet { saveSelection(projectID, key: "ApplePilotProject", uuid: true) } }
    @Published var workID = "" { didSet { saveSelection(workID, key: "ApplePilotWork", uuid: true) } }
    @Published var checkpointID = "" { didSet { saveSelection(checkpointID, key: "ApplePilotCheckpoint", uuid: true) } }
    @Published var skill = "checkpoint-review" { didSet { saveSelection(skill, key: "ApplePilotSkill", uuid: false) } }
    @Published var prompt = ""
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
    private var restoredContext: [String]?
    private var startupOpenAttempted = false
    private let validSkills: Set<String> = ["checkpoint-review", "project-summary"]
    private var resettingDraft = false
    private let python: URL
    private let service: URL
    private let state: URL
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

    init(python: URL, service: URL, state: URL, requestTimeout: TimeInterval = 90, preferences: UserDefaults? = nil) {
        self.preferences = preferences
        self.python = python
        self.service = service
        self.state = state
        self.requestTimeout = max(0.1, requestTimeout)
        if let preferences {
            StartupPreferences.migrate(preferences)
            enabled = preferences.bool(forKey: StartupPreferences.pilotEnabledKey)
            let project = preferences.string(forKey: "ApplePilotProject") ?? ""
            let work = preferences.string(forKey: "ApplePilotWork") ?? ""
            let checkpoint = preferences.string(forKey: "ApplePilotCheckpoint") ?? ""
            if canonical(project) { projectID = project }
            if canonical(work) { workID = work }
            if preferences.bool(forKey: "ApplePilotCheckpointInvalid") {
                checkpointID = checkpoint.isEmpty || canonical(checkpoint) ? "invalid" : checkpoint
            } else if canonical(checkpoint) { checkpointID = checkpoint }
            if let savedSkill = preferences.string(forKey: "ApplePilotSkill"), validSkills.contains(savedSkill) { skill = savedSkill }
            restoredContext = [projectID, workID, checkpointID, skill]
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
    var canSelect: Bool { !sessionOpen && !busy && !memoryBusy && !closing && !cleanupUnconfirmed && !hostTerminating }
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
        guard process != nil, !closing, !memoryBusy else { return }
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
