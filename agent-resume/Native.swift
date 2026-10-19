import Cocoa
import SwiftUI

final class App: NSObject, NSApplicationDelegate, ObservableObject {
    var item: NSStatusItem!
    var window: NSWindow!
    let root = CommandLine.arguments.count > 1 ? URL(fileURLWithPath: CommandLine.arguments[1]) : Bundle.main.bundleURL.deletingLastPathComponent()
    let python = CommandLine.arguments.count > 2 ? CommandLine.arguments[2] : (["/Library/Frameworks/Python.framework/Versions/Current/bin/python3", "/opt/homebrew/bin/python3", "/usr/local/bin/python3"].first { FileManager.default.isExecutableFile(atPath: $0) } ?? "/usr/bin/env")
    @Published var scheduler: [String: Any] = [:]
    @Published var snapshot: [String: Any] = [:]
    @Published var navigationNote = ""
    @Published var page = "overview"
    @Published var sessionFilter = "Récentes"
    @Published var resumeFilter = "À examiner"
    @Published var connection: [String: Any] = [:]
    @Published var socketPath = ""
    @Published var connectionBusy = false
    @Published var connectionNote = ""
    @Published var testedSocket = ""
    @Published var connectionVerified = false
    var connectionLoaded = false
    @Published var desktopConnection: [String: Any] = [:]
    @Published var desktopSocketPath = ""
    @Published var desktopConnectionBusy = false
    @Published var desktopConnectionNote = ""
    @Published var desktopTestedSocket = ""
    @Published var desktopConnectionVerified = false
    var desktopConnectionLoaded = false
    @Published var health: [String: Any] = [:]
    @Published var localSynthesis: [String: Any] = [:]
    @Published var desktopBusy = false
    @Published var desktopStartup = UserDefaults.standard.bool(forKey: "DesktopLinkedStartup")
    @Published var desktopNote = ""
    @Published var sharedBusy = false
    @Published var sharedNote = ""
    @Published var sharedSession = ""
    @Published var sharedStartup = UserDefaults.standard.bool(forKey: "SharedCodexStartup")
    var startupAttempted = false
    var refreshPending = false
    @Published var busy = false
    @Published var analysisBusy = false
    @Published var searching = false
    @Published var query = ""
    @Published var searchHasRun = false
    @Published var searchResults: [[String: Any]] = []
    @Published var searchNote = "Recherchez un diagnostic, une décision ou un nom de fichier."
    @Published var lastRefresh: Date?
    @Published var running = Set<String>()
    var rows: [[String: Any]] { snapshot["sessions"] as? [[String: Any]] ?? [] }
    var resumeTasks: [[String: Any]] { scheduler["tasks"] as? [[String: Any]] ?? [] }
    var resumeSummary: [String: Any] { scheduler["summary"] as? [String: Any] ?? [:] }
    var attention: [[String: Any]] {
        rows.filter { row in
            row["state"] as? String != "stopped" && row["is_subagent"] as? Bool != true && Date().timeIntervalSince1970 - (row["updated"] as? Double ?? 0) < 86400
        }
    }
    var analysis: [String: Any] { snapshot["analysis"] as? [String: Any] ?? [:] }
    var verdicts: [[String: Any]] {
        Array((analysis["results"] as? [String: [String: Any]] ?? [:]).values).filter { ["partial", "probable_failure"].contains($0["status"] as? String ?? "") }
    }
    static let applications: [(name: String, short: String, symbol: String, bundle: String)] = [
        ("Codex", "C", "terminal", "com.openai.codex"),
        ("Harness", "H", "hammer", "com.deepseek.dsh"),
        ("Claude Code", "CC", "chevron.left.forwardslash.chevron.right", "com.microsoft.VSCode"),
        ("Cowork", "CW", "rectangle.stack", "com.anthropic.claudefordesktop"),
        ("AutoClaw", "AC", "bolt", "com.zhipuai.autoclaw"),
    ]
    var counters: [(String, String, String, Int?)] {
        let activity = snapshot["activity"] as? [String: Any] ?? [:]

        return Self.applications.compactMap { name, short, icon, bundle in
            let count = activity[name] as? Int
            let cli = ["Codex", "Claude Code"].contains(name) && (count ?? 0) > 0
            return running.contains(bundle) || cli ? (name, short, icon, count) : nil
        }
    }
    func applicationDidFinishLaunching(_ notification: Notification) {
        if let identifier = Bundle.main.bundleIdentifier {
            for other in NSRunningApplication.runningApplications(withBundleIdentifier: identifier) where other.processIdentifier != ProcessInfo.processInfo.processIdentifier { other.terminate() }
        }
        let menu = NSMenu()
        let appMenu = NSMenu()
        appMenu.addItem(withTitle: "Quitter Reprise", action: #selector(quit), keyEquivalent: "q").target = self
        let editMenu = NSMenu(title: "Édition")
        editMenu.addItem(withTitle: "Copier", action: #selector(NSText.copy(_:)), keyEquivalent: "c")
        editMenu.addItem(withTitle: "Coller", action: #selector(NSText.paste(_:)), keyEquivalent: "v")
        editMenu.addItem(withTitle: "Tout sélectionner", action: #selector(NSText.selectAll(_:)), keyEquivalent: "a")
        let editItem = NSMenuItem(); editItem.submenu = editMenu
        let menuItem = NSMenuItem(); menuItem.submenu = appMenu; menu.addItem(menuItem); menu.addItem(editItem); NSApp.mainMenu = menu
        item = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        item.button?.image = NSImage(systemSymbolName: "arrow.clockwise.circle", accessibilityDescription: "Reprise")
        item.button?.target = self; item.button?.action = #selector(show)
        window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 1010, height: 720), styleMask: [.titled, .closable, .miniaturizable, .resizable, .fullSizeContentView], backing: .buffered, defer: false)
        window.title = "Reprise"
        window.titlebarAppearsTransparent = true
        window.toolbarStyle = .unified
        window.minSize = NSSize(width: 850, height: 590)
        window.isReleasedWhenClosed = false
        window.setFrameAutosaveName("RepriseDashboard")
        window.contentView = NSHostingView(rootView: Dashboard(app: self))
        window.center()
        NSWorkspace.shared.notificationCenter.addObserver(self, selector: #selector(reload), name: NSWorkspace.didLaunchApplicationNotification, object: nil)
        NSWorkspace.shared.notificationCenter.addObserver(self, selector: #selector(reload), name: NSWorkspace.didTerminateApplicationNotification, object: nil)
        refresh(); show(); analyzeLocal()
        Timer.scheduledTimer(withTimeInterval: 60, repeats: true) { _ in self.refresh(); self.analyzeLocal() }
    }
    @objc func show() { window.makeKeyAndOrderFront(nil); NSApp.activate(ignoringOtherApps: true) }
    @objc func quit() { NSApp.terminate(nil) }
    @objc func reload() { refresh() }
    func execute(_ args: [String]) -> [String: Any]? {
        let process = Process(); let pipe = Pipe()
        process.executableURL = URL(fileURLWithPath: python)
        process.arguments = (python == "/usr/bin/env" ? ["python3"] : []) + [root.appendingPathComponent("resume").path] + args
        process.standardOutput = pipe; process.standardError = FileHandle.nullDevice
        do {
            try process.run()
            DispatchQueue.global().asyncAfter(deadline: .now() + (["analyze", "search", "shared-mode"].contains(args.first ?? "") ? 45 : 15)) { if process.isRunning { process.terminate() } }
            let data = pipe.fileHandleForReading.readDataToEndOfFile(); process.waitUntilExit()
            guard process.terminationStatus == 0 else { return nil }
            return (try? JSONSerialization.jsonObject(with: data)) as? [String: Any]
        } catch { return nil }
    }
    func refresh() {
        running = Set(NSWorkspace.shared.runningApplications.compactMap { $0.bundleIdentifier })
        if busy { refreshPending = true; return }; busy = true
        DispatchQueue.global(qos: .utility).async {
            let result = self.execute(["detect"])
            let scheduler = self.execute(["scheduler-status"])
            let connection = self.execute(["connection-status"])
            let desktopConnection = self.execute(["desktop-connection-status"])
            let health = self.execute(["reprise-health"])
            DispatchQueue.main.async {
                self.busy = false
                self.scheduler = scheduler ?? [:]
                self.connection = connection ?? [:]
                self.desktopConnection = desktopConnection ?? [:]
                self.health = health ?? [:]
                if !self.connectionLoaded, let connection {
                    self.socketPath = connection["configured_socket"] as? String ?? (connection["candidates"] as? [[String: Any]])?.first?["path"] as? String ?? ""
                    self.connectionLoaded = true
                }
                if !self.desktopConnectionLoaded, let desktopConnection {
                    self.desktopSocketPath = desktopConnection["configured_socket"] as? String ?? (desktopConnection["candidates"] as? [[String: Any]])?.first?["path"] as? String ?? ""
                    self.desktopConnectionLoaded = true
                }
                if !self.startupAttempted {
                    self.startupAttempted = true
                    if self.sharedStartup && !self.running.contains("com.openai.codex") { self.sharedAction("launch", startup: true) }
                    if self.desktopStartup { self.desktopAction("launch"); self.desktopAction("start") }
                }
                if let result { self.snapshot = result; self.lastRefresh = Date() }
                else { self.snapshot["error"] = "Actualisation indisponible. Les dernières informations restent affichées." }
                self.item.button?.title = self.counters.map { "\($0.1) \($0.3.map(String.init) ?? "—")" }.joined(separator: " · ")
                if self.refreshPending { self.refreshPending = false; self.refresh() }
            }
        }
    }
    func openSession(_ row: [String: Any]) {
        let provider = row["provider"] as? String ?? "Codex"
        let rawID = row["id"] as? String ?? row["session"] as? String ?? ""
        let identifier = rawID.hasPrefix(provider + ":") ? String(rawID.dropFirst(provider.count + 1)) : rawID

        navigationNote = ""
        let bundle = Self.applications.first { $0.name == provider }?.bundle
        if provider == "Claude Code" {
            navigationNote = "Les journaux de Claude Code n’identifient pas son terminal ou son éditeur. Ouvrez l’hôte concerné."
            return
        }
        guard let bundle, let application = NSWorkspace.shared.urlForApplication(withBundleIdentifier: bundle) else {
            navigationNote = "Application introuvable pour cette session."
            return
        }
        let configuration = NSWorkspace.OpenConfiguration()
        configuration.activates = true
        if provider == "Codex", let uuid = UUID(uuidString: identifier), let url = URL(string: "codex://threads/" + uuid.uuidString.lowercased()) {
            NSWorkspace.shared.open([url], withApplicationAt: application, configuration: configuration) { _, error in
                if error != nil { DispatchQueue.main.async { self.navigationNote = "Impossible d’ouvrir cette session dans Codex." } }
            }
        } else {
            navigationNote = "Application affichée ; la sélection de cette session reste manuelle."
            NSWorkspace.shared.openApplication(at: application, configuration: configuration) { _, error in
                if error != nil { DispatchQueue.main.async { self.navigationNote = "Impossible d’afficher l’application." } }
            }
        }
    }
    func perform(_ args: [String], completion: @escaping ([String: Any]?) -> Void) {
        DispatchQueue.global(qos: .utility).async {
            let result = self.execute(args)
            DispatchQueue.main.async { completion(result) }
        }
    }
    func setAutomatic(_ enabled: Bool) {
        perform(["auto-mode", enabled ? "on" : "off"]) { _ in self.refresh() }
    }
    func testConnection(save: Bool = false) {
        let path = socketPath.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !path.isEmpty, !connectionBusy else { return }
        connectionBusy = true
        connectionNote = save ? "Vérification et enregistrement…" : "Vérification du serveur…"
        perform([save ? "connection-config" : "server-check", "--socket", path]) { result in
            self.connectionBusy = false
            self.testedSocket = path
            self.connectionVerified = result?["connected"] as? Bool == true
            self.connectionNote = result?["reason"] as? String ?? "Vérification indisponible. Configuration conservée."
            if save && result?["saved"] as? Bool == true { self.refresh() }
        }
    }
    func restoreConnectionDetection() {
        guard !connectionBusy else { return }
        connectionBusy = true
        perform(["connection-config", "--auto"]) { result in
            self.connectionBusy = false
            self.connectionVerified = false
            self.testedSocket = ""
            self.connectionNote = result?["reason"] as? String ?? "Configuration indisponible."
            self.refresh()
        }
    }
    func testDesktopConnection(save: Bool = false) {
        let path = desktopSocketPath.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !path.isEmpty, !desktopConnectionBusy else { return }
        desktopConnectionBusy = true
        desktopConnectionNote = save ? "Vérification et enregistrement…" : "Vérification du serveur Quick Entry…"
        perform([save ? "desktop-connection-config" : "desktop-server-check", "--socket", path]) { result in
            self.desktopConnectionBusy = false
            self.desktopTestedSocket = path
            self.desktopConnectionVerified = result?["connected"] as? Bool == true
            self.desktopConnectionNote = result?["reason"] as? String ?? "Vérification indisponible. Configuration conservée."
            if save && result?["saved"] as? Bool == true { self.refresh() }
        }
    }
    func restoreDesktopConnectionDetection() {
        guard !desktopConnectionBusy else { return }
        desktopConnectionBusy = true
        perform(["desktop-connection-config", "--auto"]) { result in
            self.desktopConnectionBusy = false
            self.desktopConnectionVerified = false
            self.desktopTestedSocket = ""
            self.desktopConnectionNote = result?["reason"] as? String ?? "Configuration indisponible."
            self.refresh()
        }
    }
    func desktopAction(_ operation: String) {
        guard !desktopBusy else { return }
        let command: String
        switch operation {
        case "launch": command = "desktop-launch"
        case "start": command = "desktop-server-start"
        case "stop": command = "desktop-server-stop"
        default: command = "desktop-status"
        }
        desktopBusy = true
        desktopNote = operation == "launch" ? "Ouverture de l’application liée…" : operation == "start" ? "Démarrage du serveur Quick Entry…" : operation == "stop" ? "Arrêt du serveur…" : "Actualisation…"
        perform([command]) { result in
            self.desktopBusy = false
            self.desktopNote = result?["reason"] as? String ?? "Opération non confirmée. Aucun nouvel essai automatique."
            self.refresh()
        }
    }
    func setDesktopStartup(_ enabled: Bool) {
        desktopStartup = enabled
        UserDefaults.standard.set(enabled, forKey: "DesktopLinkedStartup")
    }
    func sharedAction(_ operation: String, startup: Bool = false) {
        guard !sharedBusy else { return }
        var args = ["shared-mode", operation]
        if startup { args.append("--startup") }
        if operation == "check" {
            let value = sharedSession.trimmingCharacters(in: .whitespacesAndNewlines)
            let raw = value.hasPrefix("codex://threads/") ? URL(string: value)?.lastPathComponent ?? "" : value
            guard let uuid = UUID(uuidString: raw) else { sharedNote = "Collez l’identifiant UUID ou le lien codex://threads/ du chat."; return }
            args += ["--session", uuid.uuidString.lowercased()]
        }
        sharedBusy = true
        sharedNote = operation == "prepare" ? "Préparation du serveur…" : operation == "launch" ? "Lancement de Desktop…" : operation == "stop" ? "Arrêt du serveur…" : "Vérification du chat…"
        perform(args) { result in
            self.sharedBusy = false
            self.sharedNote = result?["reason"] as? String ?? "Opération non confirmée. Aucun nouvel essai automatique."
            self.refresh()
            if startup && operation == "launch" && result?["ok"] as? Bool == true {
                let shared = self.connection["shared"] as? [String: Any] ?? [:]
                if let session = shared["verified_session"] as? String {
                    self.sharedSession = session
                    DispatchQueue.main.asyncAfter(deadline: .now() + 5) { self.sharedAction("check") }
                }
            }
        }
    }
    func setSharedStartup(_ enabled: Bool) {
        sharedStartup = enabled
        UserDefaults.standard.set(enabled, forKey: "SharedCodexStartup")
    }
    func analyzeLocal() {
        guard !analysisBusy else { return }
        analysisBusy = true
        perform(["analyze"]) { _ in
            self.analysisBusy = false
            self.perform(["local-synthesis"]) { synth in
                self.localSynthesis = synth ?? [:]
                self.refresh()
            }
        }
    }
    func search() {
        let value = query.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !value.isEmpty, !searching else { return }
        searching = true
        searchNote = "Recherche locale en cours…"
        perform(["search", String(value.prefix(500))]) { result in
            self.searching = false
            guard let result else { self.searchNote = "Recherche indisponible. Vérifiez que le Trousseau est déverrouillé."; return }
            self.searchHasRun = true
            self.searchResults = result["results"] as? [[String: Any]] ?? []
            self.searchNote = result["semantic_available"] as? Bool == true ? "Mots-clés et recherche sémantique Apple · index chiffré" : "Mots-clés · modèle sémantique indisponible"
        }
    }
    func clearIndex() {
        guard !searching else { return }
        searching = true
        perform(["clear-index"]) { result in
            self.searching = false; self.searchResults = []; self.searchHasRun = false
            self.searchNote = result == nil ? "Nettoyage indisponible. Réessayez." : "Index vidé. Les sessions sont conservées."
        }
    }
}

@main
struct RepriseMain {
    static func main() {
        let application = NSApplication.shared
        application.setActivationPolicy(.accessory)
        let delegate = App()
        application.delegate = delegate
        withExtendedLifetime(delegate) { application.run() }
    }
}
