import Cocoa
import SwiftUI

final class App: NSObject, NSApplicationDelegate, ObservableObject {
    var item: NSStatusItem!
    var window: NSWindow!
    let root = CommandLine.arguments.count > 1 ? URL(fileURLWithPath: CommandLine.arguments[1]) : Bundle.main.bundleURL.deletingLastPathComponent()
    let python = CommandLine.arguments.count > 2 ? CommandLine.arguments[2] : (["/Library/Frameworks/Python.framework/Versions/Current/bin/python3", "/opt/homebrew/bin/python3", "/usr/local/bin/python3"].first { FileManager.default.isExecutableFile(atPath: $0) } ?? "/usr/bin/env")
    @Published var snapshot: [String: Any] = [:]
    @Published var page = "overview"
    @Published var sessionFilter = "Récentes"
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
    var attention: [[String: Any]] {
        rows.filter { row in
            row["state"] as? String != "stopped" && Date().timeIntervalSince1970 - (row["updated"] as? Double ?? 0) < 86400
        }
    }
    var analysis: [String: Any] { snapshot["analysis"] as? [String: Any] ?? [:] }
    var verdicts: [[String: Any]] {
        Array((analysis["results"] as? [String: [String: Any]] ?? [:]).values).filter { ["partial", "probable_failure"].contains($0["status"] as? String ?? "") }
    }
    var counters: [(String, String, String, Int?)] {
        let activity = snapshot["activity"] as? [String: Any] ?? [:]
        let entries = [("Codex", "C", "terminal", "com.openai.codex"), ("Harness", "H", "hammer", "com.deepseek.dsh"), ("Claude Code", "CC", "chevron.left.forwardslash.chevron.right", "com.microsoft.VSCode"), ("Cowork", "CW", "rectangle.stack", "com.anthropic.claudefordesktop"), ("AutoClaw", "AC", "bolt", "com.zhipuai.autoclaw")]
        return entries.compactMap { name, short, icon, bundle in
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
            DispatchQueue.global().asyncAfter(deadline: .now() + (["analyze", "search"].contains(args.first ?? "") ? 45 : 15)) { if process.isRunning { process.terminate() } }
            let data = pipe.fileHandleForReading.readDataToEndOfFile(); process.waitUntilExit()
            guard process.terminationStatus == 0 else { return nil }
            return (try? JSONSerialization.jsonObject(with: data)) as? [String: Any]
        } catch { return nil }
    }
    func refresh() {
        running = Set(NSWorkspace.shared.runningApplications.compactMap { $0.bundleIdentifier })
        if busy { return }; busy = true
        DispatchQueue.global(qos: .utility).async {
            let result = self.execute(["detect"])
            DispatchQueue.main.async {
                self.busy = false
                if let result { self.snapshot = result; self.lastRefresh = Date() }
                else { self.snapshot["error"] = "Actualisation indisponible. Les dernières informations restent affichées." }
                self.item.button?.title = self.counters.map { "\($0.1) \($0.3.map(String.init) ?? "—")" }.joined(separator: " · ")
            }
        }
    }
    func analyzeLocal() {
        if analysisBusy { return }; analysisBusy = true
        DispatchQueue.global(qos: .utility).async {
            _ = self.execute(["analyze"])
            DispatchQueue.main.async { self.analysisBusy = false; self.refresh() }
        }
    }
    func search() {
        let value = query.trimmingCharacters(in: .whitespacesAndNewlines)
        if value.isEmpty || searching { return }; searching = true; searchNote = "Recherche locale en cours…"
        DispatchQueue.global(qos: .utility).async {
            let result = self.execute(["search", String(value.prefix(500))])
            DispatchQueue.main.async {
                self.searching = false
                guard let result else { self.searchNote = "Recherche indisponible. Vérifiez que le Trousseau est déverrouillé."; return }
                self.searchHasRun = true
                self.searchResults = result["results"] as? [[String: Any]] ?? []
                self.searchNote = result["semantic_available"] as? Bool == true ? "Mots-clés et recherche sémantique Apple · index chiffré" : "Mots-clés · modèle sémantique indisponible"
            }
        }
    }
    func clearIndex() {
        if searching { return }; searching = true
        DispatchQueue.global(qos: .utility).async {
            let result = self.execute(["clear-index"])
            DispatchQueue.main.async {
                self.searching = false; self.searchResults = []; self.searchHasRun = false
                self.searchNote = result == nil ? "Nettoyage indisponible. Réessayez." : "Index vidé. Les sessions sont conservées."
            }
        }
    }
}

struct Surface<Content: View>: View {
    @ViewBuilder var content: Content
    var body: some View { content.padding(22).frame(maxWidth: .infinity, alignment: .leading).background(.background, in: RoundedRectangle(cornerRadius: 18)).overlay(RoundedRectangle(cornerRadius: 18).strokeBorder(.primary.opacity(0.06))) }
}
struct Dashboard: View {
    @ObservedObject var app: App
    let sections = [("overview", "Vue d’ensemble", "square.grid.2x2"), ("sessions", "Sessions", "tray"), ("analysis", "Analyse locale", "sparkles"), ("search", "Recherche", "magnifyingglass"), ("privacy", "Confidentialité", "lock.shield")]
    var title: String { sections.first { $0.0 == app.page }?.1 ?? "Reprise" }
    var body: some View {
        NavigationSplitView {
            VStack(alignment: .leading, spacing: 0) {
                HStack(spacing: 10) { Image(systemName: "arrow.clockwise.circle.fill").font(.title2).foregroundStyle(.primary); Text("Reprise").font(.title2.weight(.semibold)) }.padding(20)
                List(selection: Binding<String?>(get: { app.page }, set: { app.page = $0 ?? "overview" })) {
                    Section("Activité") {
                        ForEach(sections, id: \.0) { entry in
                            HStack { Label(entry.1, systemImage: entry.2); Spacer(); if entry.0 == "sessions" && !app.attention.isEmpty { Text("\(app.attention.count)").font(.caption.monospacedDigit()).foregroundStyle(.secondary) } }.tag(entry.0)
                        }
                    }
                }.listStyle(.sidebar)
                Label("Privé, sur ce Mac", systemImage: "lock").font(.caption).foregroundStyle(.secondary).padding(20)
            }.navigationSplitViewColumnWidth(min: 200, ideal: 220, max: 260)
        } detail: {
            ScrollView {
                VStack(alignment: .leading, spacing: 24) {
                    VStack(alignment: .leading, spacing: 6) { Text(app.page == "overview" ? "Votre activité" : title).font(.system(size: 28, weight: .bold)); Text(subtitle).foregroundStyle(.secondary) }
                    if let error = app.snapshot["error"] as? String { Label(error, systemImage: "info.circle").font(.callout).foregroundStyle(.secondary) }
                    switch app.page {
                    case "sessions": sessions
                    case "analysis": analysis
                    case "search": search
                    case "privacy": privacy
                    default: overview
                    }
                }.padding(28).frame(maxWidth: 1100, alignment: .leading)
            }.background(Color(nsColor: .windowBackgroundColor)).navigationTitle(title)
            .toolbar {
                ToolbarItemGroup {
                    if app.busy { ProgressView().controlSize(.small) }
                    Button { app.page = "search" } label: { Label("Rechercher", systemImage: "magnifyingglass") }.help("Recherche privée").keyboardShortcut("f", modifiers: .command)
                    Button { app.refresh() } label: { Label("Actualiser", systemImage: "arrow.clockwise") }.disabled(app.busy).keyboardShortcut("r", modifiers: .command)
                }
            }
            .safeAreaInset(edge: .bottom) {
                HStack { Label("Lecture locale", systemImage: "internaldrive"); Spacer(); if let date = app.lastRefresh { Text("Actualisé à " + date.formatted(date: .omitted, time: .shortened)) } }.font(.caption).foregroundStyle(.secondary).padding(.horizontal, 24).padding(.vertical, 10).background(.bar)
            }
        }
    }
    var subtitle: String {
        switch app.page {
        case "sessions": return "Distinguez les interruptions récentes des arrêts et de l’historique."
        case "analysis": return "Un second regard sur les comptes rendus, sur votre Mac."
        case "search": return "Retrouvez un élément sans vous souvenir de l’application."
        case "privacy": return "Vos données de recherche restent sous votre contrôle."
        default: return "L’essentiel de vos sessions, sans les interrompre."
        }
    }
    var overview: some View {
        VStack(alignment: .leading, spacing: 20) {
            if app.lastRefresh == nil && app.busy {
                Surface { HStack(spacing: 12) { ProgressView().controlSize(.small); Text("Lecture de l’activité locale…").foregroundStyle(.secondary) } }
            } else {
                Surface {
                    HStack(alignment: .center, spacing: 18) {
                        Image(systemName: app.attention.isEmpty ? "clock" : "tray")
                            .font(.system(size: 30, weight: .light)).foregroundStyle(.secondary).accessibilityHidden(true)
                        VStack(alignment: .leading, spacing: 6) {
                            Text(app.attention.isEmpty ? "Aucune interruption récente détectée" : "\(app.attention.count) session\(app.attention.count > 1 ? "s" : "") à examiner").font(.title3.weight(.semibold))
                            Text(app.attention.isEmpty ? "Les sessions plus anciennes restent dans l’historique." : "Signalements des dernières 24 heures, hors arrêts volontaires.").font(.callout).foregroundStyle(.secondary)
                        }
                        Spacer(minLength: 8)
                        Button(app.attention.isEmpty ? "Historique" : "Examiner") { app.page = "sessions"; app.sessionFilter = app.attention.isEmpty ? "Toutes" : "Récentes" }
                            .buttonStyle(.bordered)
                    }
                }
                Surface {
                    VStack(alignment: .leading, spacing: 16) {
                        HStack {
                            Text("Applications ouvertes").font(.headline)
                            Spacer()
                            Text("Activité estimée").font(.caption).foregroundStyle(.secondary)
                        }
                        if app.counters.isEmpty { Text("Aucune application suivie ouverte.").foregroundStyle(.secondary) }
                        ForEach(app.counters, id: \.0) { entry in
                            HStack(spacing: 12) {
                                Image(systemName: entry.2).frame(width: 22).foregroundStyle(.secondary).accessibilityHidden(true)
                                Text(entry.0)
                                Spacer()
                                if let count = entry.3 {
                                    if count > 0 {
                                        Text("\(count) en cours").font(.callout.weight(.semibold)).monospacedDigit()
                                            .padding(.horizontal, 10).padding(.vertical, 5).background(Color.accentColor.opacity(0.10), in: Capsule())
                                    } else { Text("Au repos").font(.callout).foregroundStyle(.secondary) }
                                } else { Text("État indisponible").font(.callout).foregroundStyle(.secondary) }
                            }.accessibilityElement(children: .combine)
                        }
                    }
                }
                Surface {
                    VStack(alignment: .leading, spacing: 18) {
                        HStack { Text("Disponibilité Codex").font(.headline); Spacer(); Text("Quota du compte").font(.caption).foregroundStyle(.secondary) }
                        if let quota = app.snapshot["codex_quota"] as? [String: Any], let rates = quota["rates"] as? [String: Any] {
                            HStack(alignment: .top, spacing: 28) {
                                quotaRow("Fenêtre courte", value: rates["primary"] as? [String: Any])
                                quotaRow("Semaine", value: rates["secondary"] as? [String: Any])
                            }
                            if let stamp = quota["measured_at"] as? String { Text("Mesure du " + friendlyDate(stamp)).font(.caption).foregroundStyle(.secondary) }
                        } else { Text("Aucune mesure locale disponible.").foregroundStyle(.secondary) }
                    }
                }
                HStack(spacing: 8) {
                    Image(systemName: "info.circle").accessibilityHidden(true)
                    Text("Un agent silencieux peut rester actif. Les estimations reflètent les deux dernières minutes.")
                }.font(.caption).foregroundStyle(.secondary)
            }
        }
    }
    func quotaRow(_ name: String, value: [String: Any]?) -> some View {
        VStack(alignment: .leading, spacing: 7) {
            if let value, let used = value["used_percent"] as? Double, let reset = value["resets_at"] as? Double {
                let remaining = max(0, min(100, 100-used)); let date = Date(timeIntervalSince1970: reset)
                Text(name).font(.callout).foregroundStyle(.secondary)
                Text(date < Date() ? "Mesure expirée" : "\(Int(remaining)) %").font(.system(size: 27, weight: .semibold, design: .rounded)).monospacedDigit()
                if date >= Date() { Text("disponibles").font(.caption).foregroundStyle(.secondary) }
                if date >= Date() { ProgressView(value: remaining, total: 100).tint(remaining < 15 ? .orange : .accentColor); Text("Renouvellement " + date.formatted(date: .abbreviated, time: .shortened)).font(.caption).foregroundStyle(.secondary) }
            } else { Text(name + " · indisponible").foregroundStyle(.secondary) }
        }
    }
    var sessions: some View {
        VStack(alignment: .leading, spacing: 16) {
            Picker("Filtrer", selection: $app.sessionFilter) { ForEach(["Récentes", "Toutes", "Erreurs", "Arrêts", "À examiner"], id: \.self) { Text($0) } }.pickerStyle(.segmented).frame(maxWidth: 460)
            let visible = app.rows.filter { row in
                let state = row["state"] as? String ?? ""
                return (app.sessionFilter == "Récentes" && app.attention.contains { ($0["id"] as? String) == (row["id"] as? String) }) || app.sessionFilter == "Toutes" || (app.sessionFilter == "Erreurs" && ["failed", "quota_candidate", "rate_limited"].contains(state)) || (app.sessionFilter == "Arrêts" && state == "stopped") || (app.sessionFilter == "À examiner" && ["unfinished", "needs_user"].contains(state))
            }
            if visible.isEmpty { empty("Aucun signalement dans cette vue", icon: "tray", detail: app.sessionFilter == "Récentes" ? "Aucune interruption détectée sur les dernières 24 heures. Consultez Toutes pour l’historique." : "Essayez un autre filtre pour consulter les autres sessions.") }
            ForEach(Array(visible.enumerated()), id: \.offset) { _, row in
                Surface {
                    DisclosureGroup {
                        VStack(alignment: .leading, spacing: 10) {
                            Text(row["label"] as? String ?? "À vérifier")
                            if let stamp = row["updated"] as? Double { Text(Date(timeIntervalSince1970: stamp).formatted(date: .abbreviated, time: .shortened)).foregroundStyle(.secondary) }
                            if let tokens = row["tokens"] as? Double { Text("Tokens enregistrés : \(Int(min(tokens, 1e15)))") }
                            if let cost = row["cost_usd"] as? Double, cost > 0 { Text(String(format: "Estimation enregistrée : %.4f $", cost)) }
                            Text(row["id"] as? String ?? "").font(.caption.monospaced()).foregroundStyle(.secondary).textSelection(.enabled)
                            Text("Aucune reprise automatique sur la seule base de ce signalement.").font(.caption).foregroundStyle(.secondary)
                        }.padding(.top, 12)
                    } label: {
                        HStack(spacing: 12) {
                            Image(systemName: row["state"] as? String == "stopped" ? "pause.circle" : "exclamationmark.circle").font(.title2).foregroundStyle(.secondary)
                            VStack(alignment: .leading, spacing: 4) { Text(sessionTitle(row)).font(.headline); Text((row["provider"] as? String ?? "") + " · " + ((row["updated"] as? Double).map { Date(timeIntervalSince1970: $0).formatted(date: .abbreviated, time: .shortened) } ?? "Date inconnue")).font(.caption).foregroundStyle(.secondary) }
                            Spacer(); Text(row["state"] as? String == "stopped" ? "Arrêtée" : "À vérifier").font(.caption).foregroundStyle(.secondary)
                        }
                    }
                }
            }
            Text("Les arrêts volontaires sont séparés des échecs. Cette liste conserve jusqu’à 20 sessions récentes.").font(.caption).foregroundStyle(.secondary)
        }
    }
    var analysis: some View {
        VStack(alignment: .leading, spacing: 18) {
            Surface { HStack { VStack(alignment: .leading, spacing: 6) { Label("Apple Intelligence", systemImage: "sparkles").font(.headline); Text(app.analysis["status"] as? String ?? "En attente").font(.callout).foregroundStyle(.secondary) }; Spacer(); Button(app.analysisBusy ? "Analyse…" : "Analyser") { app.analyzeLocal() }.disabled(app.analysisBusy) } }
            if app.verdicts.isEmpty { empty("Aucun signalement à afficher", icon: "text.badge.checkmark", detail: "L’absence de signalement ne garantit pas la réussite d’une session. Les vérifications du projet restent la référence.") }
            ForEach(Array(app.verdicts.enumerated()), id: \.offset) { _, value in Surface { VStack(alignment: .leading, spacing: 10) { Text(value["title"] as? String ?? "Session").font(.headline); Text(value["reason"] as? String ?? ""); Text(value["evidence"] as? String ?? "").font(.callout).foregroundStyle(.secondary).textSelection(.enabled) } } }
            Text("Codex et Claude Code · un nouveau compte rendu au maximum par minute · aucune relance déclenchée par le modèle.").font(.caption).foregroundStyle(.secondary)
        }
    }
    var search: some View {
        VStack(alignment: .leading, spacing: 18) {
            HStack(spacing: 12) { Image(systemName: "magnifyingglass").foregroundStyle(.secondary); TextField("Tests Rust, quota, nom de fichier…", text: $app.query).textFieldStyle(.plain).onSubmit { app.search() }; if app.searching { ProgressView().controlSize(.small) }; Button("Rechercher") { app.search() }.disabled(app.searching || app.query.trimmingCharacters(in: .whitespaces).isEmpty).buttonStyle(.borderedProminent) }.padding(14).background(.background, in: RoundedRectangle(cornerRadius: 14))
            Text(app.searchNote).font(.callout).foregroundStyle(.secondary)
            if app.searchResults.isEmpty && !app.searching {
                empty(app.searchHasRun ? "Aucun résultat pour cette recherche" : "Retrouvez le fil d’une session", icon: "doc.text.magnifyingglass", detail: app.searchHasRun ? "Essayez un terme plus court ou un code d’erreur exact." : "Recherchez dans les états récents et comptes rendus analysés, toutes applications confondues.")
            }
            ForEach(Array(app.searchResults.enumerated()), id: \.offset) { _, value in Surface { VStack(alignment: .leading, spacing: 8) { Text(value["title"] as? String ?? "Session").font(.headline); Text((value["provider"] as? String ?? "") + " · " + String((value["id"] as? String ?? "").suffix(8))).font(.caption).foregroundStyle(.secondary); Text(value["text"] as? String ?? "").textSelection(.enabled) } } }
        }
    }
    var privacy: some View {
        VStack(alignment: .leading, spacing: 18) {
            Surface { VStack(alignment: .leading, spacing: 16) { Label("Index et analyses chiffrés", systemImage: "lock.shield").font(.headline); Text("AES-256-GCM avec CryptoKit. La clé dédiée reste dans le Trousseau macOS. Les données de recherche sont déchiffrées en mémoire."); Divider(); Label("Sans publication Spotlight", systemImage: "eye.slash"); Label("Modèles Apple sur l’appareil", systemImage: "desktopcomputer"); Label("Purge quotidienne après sept jours", systemImage: "calendar") } }
            Surface { VStack(alignment: .leading, spacing: 12) { Text("Cache de recherche").font(.headline); Text("Vider l’index conserve les sessions. La prochaine recherche reconstruit les éléments récents.").foregroundStyle(.secondary); Button("Vider l’index") { app.clearIndex() }.disabled(app.searching); if app.searchNote.contains("Index vidé") { Text(app.searchNote).font(.caption).foregroundStyle(.secondary) } } }
            Text("Le chiffrement concerne l’index et le cache d’analyse. Les instructions du scheduler, ses journaux et ses archives ZIP ne sont pas chiffrés par ce mécanisme. Les sources des autres applications restent inchangées.").font(.caption).foregroundStyle(.secondary)
        }
    }
    func empty(_ title: String, icon: String, detail: String) -> some View {
        VStack(spacing: 12) { Image(systemName: icon).font(.system(size: 36, weight: .light)).foregroundStyle(.secondary); Text(title).font(.title3.weight(.semibold)); Text(detail).foregroundStyle(.secondary).multilineTextAlignment(.center).frame(maxWidth: 380) }.frame(maxWidth: .infinity).padding(.vertical, 44)
    }
    func sessionTitle(_ row: [String: Any]) -> String {
        let title = row["title"] as? String ?? "Session"
        return title.hasPrefix("Session ") ? title + " · " + String((row["id"] as? String ?? "").suffix(6)) : title
    }
    func friendlyDate(_ raw: String) -> String {
        let format = ISO8601DateFormatter(); format.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        return format.date(from: raw)?.formatted(date: .abbreviated, time: .shortened) ?? raw
    }
}
let application = NSApplication.shared
application.setActivationPolicy(.accessory)
let delegate = App()
application.delegate = delegate
application.run()
