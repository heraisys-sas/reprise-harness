import Cocoa
import SwiftUI

struct Surface<Content: View>: View {
    @ViewBuilder var content: Content
    var body: some View {
        content
            .padding(22)
            .frame(maxWidth: .infinity, alignment: .leading)
            .glassEffect(.regular, in: .rect(cornerRadius: 18))
            .overlay(RoundedRectangle(cornerRadius: 18).strokeBorder(.primary.opacity(0.07)))
    }
}

// A settings block answers "what does this do?" before showing the controls.
// The state badge lands on the title line so a screen can be scanned without
// reading any body text.
struct SettingsCard<Content: View>: View {
    var title: String
    var summary: String?
    var status: String?
    var ok: Bool = true
    @ViewBuilder var content: Content
    var body: some View {
        Surface {
            VStack(alignment: .leading, spacing: 14) {
                HStack(alignment: .firstTextBaseline, spacing: 10) {
                    Text(title).font(.headline)
                    Spacer(minLength: 8)
                    if let status {
                        Label(status, systemImage: ok ? "checkmark.circle.fill" : "exclamationmark.circle.fill")
                            .font(.caption.weight(.medium))
                            .foregroundStyle(ok ? Color.green : Color.orange)
                            .accessibilityLabel(title + " : " + status)
                    }
                }
                if let summary { Text(summary).font(.callout).foregroundStyle(.secondary) }
                content
            }
        }
    }
}

// A control keeps one visible line of consequence; the long justification is
// preserved verbatim behind a disclosure instead of running beside every switch.
struct SettingsHint: View {
    var text: String
    var detail: String?
    var detailLabel: String = "Détail"
    var body: some View {
        if let detail {
            DisclosureGroup {
                Text(detail)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(.top, 6)
                    .textSelection(.enabled)
            } label: {
                HStack(alignment: .firstTextBaseline, spacing: 6) {
                    Text(text).font(.caption).foregroundStyle(.secondary)
                    Text(detailLabel).font(.caption.weight(.medium)).foregroundStyle(.secondary)
                }
            }
            .accessibilityLabel(text + " " + detailLabel + " disponible.")
        } else {
            Text(text).font(.caption).foregroundStyle(.secondary)
        }
    }
}

// One submission route per line: state badge on the right, single secondary
// reason line below. Read-only by design — activation lives in configuration.
struct RouteLine: View {
    var title: String
    var route: [String: Any]
    var fallbackDetail: String

    // Badge derived from the daemon-reported state; no static title ever
    // claims a route is ready (#229).
    private var badge: (label: String, color: Color) {
        let state = route["state"] as? String ?? ""
        if route["enabled"] as? Bool == true {
            switch state {
            case "prête", "disponible": return ("Prête", Color.green)
            case "détectée": return ("Détectée · non vérifiée", Color.orange)
            default: return ("Indisponible", Color.orange)
            }
        }
        return ("Désactivée", Color.secondary)
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack(alignment: .firstTextBaseline, spacing: 10) {
                Text(title).font(.callout.weight(.semibold))
                Spacer(minLength: 8)
                Text(badge.label)
                    .font(.caption.weight(.medium))
                    .foregroundStyle(badge.color)
                    .padding(.horizontal, 8).padding(.vertical, 3)
                    .background(badge.color.opacity(0.12), in: Capsule())
                    .accessibilityLabel(title + " : " + badge.label)
            }
            Text((route["reason"] as? String).flatMap { $0.isEmpty ? nil : $0 } ?? fallbackDetail)
                .font(.caption).foregroundStyle(.secondary)
        }
    }
}

// Screen-level verdict, placed before any control: the only question asked when
// opening a settings screen is "is anything wrong right now?".
struct SettingsBanner: View {
    var ok: Bool
    var title: String
    var detail: String
    var body: some View {
        HStack(alignment: .center, spacing: 14) {
            Image(systemName: ok ? "checkmark.seal.fill" : "exclamationmark.triangle.fill")
                .font(.title2)
                .foregroundStyle(ok ? Color.green : Color.orange)
                .accessibilityHidden(true)
            VStack(alignment: .leading, spacing: 3) {
                Text(title).font(.headline)
                Text(detail).font(.callout).foregroundStyle(.secondary).textSelection(.enabled)
            }
            Spacer(minLength: 0)
        }
        .padding(16)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background((ok ? Color.green : Color.orange).opacity(0.10), in: RoundedRectangle(cornerRadius: 14))
        .accessibilityElement(children: .combine)
        .accessibilityLabel(title + ". " + detail)
    }
}

// Inline progress / result line for long-running actions: the busy flag runs the
// spinner, the message carries the visible state, and both are announced together.
struct InlineStatus: View {
    var busy: Bool
    var message: String
    var body: some View {
        HStack(alignment: .firstTextBaseline, spacing: 8) {
            if busy { ProgressView().controlSize(.small) } else { Image(systemName: "info.circle") }
            Text(message).textSelection(.enabled)
        }
        .font(.callout)
        .foregroundStyle(.secondary)
        .accessibilityElement(children: .combine)
        .accessibilityLabel((busy ? "En cours : " : "") + message)
        .accessibilityAddTraits(.updatesFrequently)
    }
}

// Result of a presence check: a text sign plus a full spoken label, so colour is
// never the only signal.
struct PresenceCheckLabel: View {
    var present: Bool
    var item: String
    var body: some View {
        Image(systemName: present ? "checkmark.circle" : "xmark.circle")
            .foregroundStyle(present ? .green : .red)
            .accessibilityLabel(item + (present ? " : présent" : " : absent"))
    }
}

// Session state marker: readable glyph and spoken sentence instead of colour.
struct SessionStateMark: View {
    var stopped: Bool
    var body: some View {
        Image(systemName: stopped ? "pause.circle" : "exclamationmark.circle")
            .font(.title2)
            .foregroundStyle(.secondary)
            .accessibilityLabel(stopped ? "Session arrêtée" : "Session à vérifier")
    }
}

// Socket candidate marker: the glyph alone means nothing to VoiceOver.
struct SocketCandidateMark: View {
    var detected: Bool
    var body: some View {
        Image(systemName: detected ? "circle.fill" : "circle")
            .font(.caption)
            .foregroundStyle(.secondary)
            .padding(.top, 4)
            .accessibilityLabel(detected ? "Socket détecté" : "Socket indisponible")
    }
}
struct Dashboard: View {
    @ObservedObject var app: App
    @StateObject private var applePilot: ApplePilotUI
    init(app: App) {
        self.app = app
        _applePilot = StateObject(wrappedValue: ApplePilotUI.make(app: app))
    }
    // Quatre destinations (issue #263 et refonte : Écosystème, Connexions, Santé
    // et Confidentialité vivent dans Intégrations ; Reprises, Analyse locale et
    // les fils Codex vivent dans Sessions ; la Recherche devient une feuille).
    let sections = [("overview", "Vue d’ensemble", "square.grid.2x2"), ("sessions", "Sessions", "tray"), ("apple-pilot", "Pilote Apple", "bubble.left.and.text.bubble.right"), ("integrations", "Intégrations", "puzzlepiece.extension")]
    func sidebarEntry(_ entry: (String, String, String)) -> some View {
        HStack {
            Label(entry.1, systemImage: entry.2)
            Spacer()
            if let count = sidebarBadge(entry.0), count > 0 {
                Text("\(count)")
                    .font(.caption.monospacedDigit())
                    .foregroundStyle(.secondary)
                    .accessibilityLabel("\(entry.1) : \(count) éléments à examiner")
            }
        }
        .contentShape(Rectangle())
        .tag(entry.0)
    }
    var title: String { sections.first { $0.0 == app.page }?.1 ?? "Reprise" }
    func sectionIntro(_ title: String, _ detail: String) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(title).font(.title3.weight(.semibold))
            Text(detail).font(.callout).foregroundStyle(.secondary)
        }
    }
    func sectionTarget(_ page: String, _ tab: String? = nil) {
        if let tab { app.connectionTab = tab }
        app.page = page
    }
    var searchSheetView: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack {
                Label("Recherche privée", systemImage: "magnifyingglass").font(.title3.weight(.semibold))
                Spacer()
                Button("Fermer") { app.searchSheet = false }.keyboardShortcut(.escape)
            }
            search
            Spacer()
        }
        .padding(20)
        .frame(minWidth: 620, minHeight: 420)
    }
    @ViewBuilder var body: some View {
        if app.stateReady {
            normalDashboard
                .onAppear { loadPilotStartupProjects() }
                .onChange(of: app.setupBusy) { _, busy in if !busy { loadPilotStartupProjects() } }
                .onReceive(app.$projects) { rows in
                    applePilot.updateKnownProjects(Set(rows.compactMap { $0["id"] as? String }))
                }
        } else { StateSetup(app: app) }
    }
    private func loadPilotStartupProjects() {
        guard !app.pilotProjectsRequested, app.stateReady, !app.setupBusy, app.runtimeError == nil else { return }
        app.pilotProjectsRequested = true
        app.setupCommand(["project-list"]) { result in
            guard result["status"] as? String == "ok", let rows = result["projects"] as? [[String: Any]] else {
                app.pilotProjectsLoadFailed = true
                return
            }
            app.pilotProjectsLoadFailed = false
            app.projects = rows
            applePilot.updateKnownProjects(Set(rows.compactMap { $0["id"] as? String }))
            applePilot.openRestoredSessionIfReady()
        }
    }
    var normalDashboard: some View {
        NavigationSplitView {
            VStack(alignment: .leading, spacing: 0) {
                HStack(spacing: 10) { Image(systemName: "arrow.clockwise.circle.fill").font(.title2).foregroundStyle(.primary); Text("Reprise").font(.title2.weight(.semibold)) }.padding(20)
                List(selection: Binding<String?>(get: { app.page }, set: { app.page = $0 ?? "overview" })) {
                    Section("Suivre") {
                        ForEach(sections.prefix(2), id: \.0) { sidebarEntry($0) }
                    }
                    Section("Piloter") {
                        ForEach(sections.suffix(2), id: \.0) { sidebarEntry($0) }
                    }
                }.listStyle(.sidebar).frame(maxHeight: .infinity)
                Label("Privé, sur ce Mac", systemImage: "lock").font(.caption).foregroundStyle(.secondary).padding(20)
            }.navigationSplitViewColumnWidth(min: 200, ideal: 220, max: 260)
        } detail: {
            ScrollView {
                VStack(alignment: .leading, spacing: 24) {
                    VStack(alignment: .leading, spacing: 6) { Text(app.page == "overview" ? "Votre activité" : title).font(.system(size: 28, weight: .bold)); Text(subtitle).foregroundStyle(.secondary) }
                    if let error = app.snapshot["error"] as? String { Label(error, systemImage: "info.circle").font(.callout).foregroundStyle(.secondary) }
                    if !app.navigationNote.isEmpty { Label(app.navigationNote, systemImage: "info.circle").font(.callout).foregroundStyle(.secondary) }
                    switch app.page {
                    case "sessions":
                        VStack(alignment: .leading, spacing: 32) {
                            sessions
                            Divider()
                            sectionIntro("Reprises", "Les tentatives sont regroupées par cause ; les détails restent accessibles à la demande.")
                            resumptions
                            Divider()
                            sectionIntro("Second regard", "Un autre examen des comptes rendus, sur votre Mac.")
                            analysis
                            Divider()
                            sectionIntro("Conversations Codex", "Les fils récents du service Codex, lus localement.")
                            CodexThreadsSection(app: app)
                        }
                    case "apple-pilot": ApplePilotView(app: app, pilot: applePilot, retryStartupProjects: {
                        app.pilotProjectsRequested = false
                        loadPilotStartupProjects()
                    })
                    case "integrations":
                        VStack(alignment: .leading, spacing: 32) {
                            HStack {
                                Button { app.verifyEverything() } label: { Label("Vérifier tout", systemImage: "checkmark.seal") }
                                    .disabled(app.busy || app.verifyEverythingBusy).help("Relancer toutes les vérifications de cette page")
                                if app.verifyEverythingBusy { InlineStatus(busy: true, message: "Vérification en cours…") }
                            }
                            if !app.verifyEverythingSummary.isEmpty && !app.verifyEverythingBusy {
                                Text(app.verifyEverythingSummary).font(.callout).foregroundStyle(.secondary)
                            }
                            quotaGauges
                            Integrations(app: app)
                            Divider()
                            sectionIntro("Liaisons entre applications", "Comprenez les liaisons et ouvrez leur diagnostic.")
                            ecosystem
                            Divider()
                            sectionIntro("Connexions", "Configurez l’accès aux services Codex, Claude Desktop et IronClaw.")
                            connections
                            Divider()
                            sectionIntro("Après une mise à jour", "Ce qu’il faut relancer, ce qui reste à recontrôler, et les actions humaines.")
                            health
                            Divider()
                            sectionIntro("Confidentialité", "Vos données restent sous votre contrôle.")
                            privacy
                        }
                    default: overview
                    }
                }.padding(28).frame(maxWidth: 1100, alignment: .leading)
            }.background(Color(nsColor: .windowBackgroundColor)).navigationTitle(title)
            .toolbar {
                ToolbarItemGroup {
                    if app.busy { ProgressView().controlSize(.small) }
                    Button { app.searchSheet = true } label: { Label("Rechercher", systemImage: "magnifyingglass") }.help("Recherche privée").keyboardShortcut("f", modifiers: .command)
                    Button { app.refresh() } label: { Label("Actualiser", systemImage: "arrow.clockwise") }.disabled(app.busy).keyboardShortcut("r", modifiers: .command).accessibilityLabel("Actualiser les données")
                    Menu {
                        Button("Relancer Reprise") { app.relaunchApp() }.disabled(app.relaunchBusy)
                        Button("Quitter Reprise") { NSApp.terminate(nil) }
                    } label: { Label("Reprise", systemImage: "power") }.help("Arrêt et relance de Reprise")
                }
            }
            .sheet(isPresented: $app.searchSheet) { searchSheetView }
            .safeAreaInset(edge: .bottom) {
                HStack { Label("Lecture locale", systemImage: "internaldrive"); Spacer(); if let date = app.lastRefresh { Text("Actualisé à " + localTime(date)) } }.font(.caption).foregroundStyle(.secondary).padding(.horizontal, 24).padding(.vertical, 10).background(.bar)
            }
        }
    }
    var subtitle: String {
        switch app.page {
        case "ecosystem": return "Comprenez les liaisons et ouvrez leur diagnostic."
        case "sessions": return "Distinguez les interruptions récentes des arrêts et de l’historique."
        case "resumptions": return "Les tentatives sont regroupées par cause. Les détails restent accessibles à la demande."
        case "analysis": return "Un second regard sur les comptes rendus, sur votre Mac."
        case "apple-pilot": return "Un assistant local expérimental pour examiner un contexte choisi."
        case "search": return "Retrouvez un élément sans vous souvenir de l’application."
        case "connections": return "Connectez Reprise aux sockets codexd (codex-desktop) et Quick Apply (claude-desktopd)."
        case "integrations": return "Préparez explicitement les outils MCP et les consignes de votre profil Harness."
        case "health": return "Après une mise à jour : ce qu’il faut relancer, ce qui reste à parser, et les actions humaines."
        case "privacy": return "Vos données de recherche restent sous votre contrôle."
        default: return "L’essentiel de vos sessions, sans les interrompre."
        }
    }
    private func ecosystemColor(_ level: EcosystemStatus.Level) -> Color {
        switch level {
        case .ready: return .green
        case .attention: return .orange
        case .problem: return .red
        }
    }
    private func ecosystemLink(_ target: String, symbol: String, value: EcosystemStatus.Value,
                               action: @escaping () -> Void) -> some View {
        let color = ecosystemColor(value.level)
        return HStack(alignment: .center, spacing: 0) {
            Rectangle().fill(color).frame(width: 24, height: 2).accessibilityHidden(true)
            Image(systemName: symbol).font(.system(size: 15, weight: .medium))
                .foregroundStyle(color).frame(width: 32, height: 32)
                .background(color.opacity(0.1), in: Circle()).accessibilityHidden(true)
            VStack(alignment: .leading, spacing: 4) {
                HStack(alignment: .firstTextBaseline, spacing: 8) {
                    Button(target, action: action).buttonStyle(.link).font(.headline)
                    Spacer(minLength: 8)
                    Text(value.label).font(.caption.weight(.semibold)).foregroundStyle(color)
                        .multilineTextAlignment(.trailing)
                }
                Text(value.detail).font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            }.padding(.leading, 10)
        }.accessibilityElement(children: .contain)
    }
    private func applicationPresent(_ bundleID: String) -> Bool {
        if app.running.contains(bundleID) { return true }
        guard let url = NSWorkspace.shared.urlForApplication(withBundleIdentifier: bundleID) else { return false }
        return FileManager.default.fileExists(atPath: url.path)
    }
    private func detectedSocket(_ connection: [String: Any]) -> Bool {
        (connection["candidates"] as? [[String: Any]] ?? []).contains { $0["detected"] as? Bool == true }
    }
    private var ironclawConfigured: Bool {
        let state: URL
        if let override = ProcessInfo.processInfo.environment["AGENT_RESUME_STATE"], !override.isEmpty {
            state = URL(fileURLWithPath: (override as NSString).expandingTildeInPath, isDirectory: true)
        } else if app.usesBundledRuntime {
            state = FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent("Library/Application Support/Reprise/state", isDirectory: true)
        } else {
            state = app.root.appendingPathComponent("state", isDirectory: true)
        }
        return FileManager.default.fileExists(atPath: state.appendingPathComponent("ironclaw-endpoint.json").path)
    }
    var ecosystem: some View {
        let codexMatches = app.testedSocket == app.socketPath.trimmingCharacters(in: .whitespacesAndNewlines)
        let desktopMatches = app.desktopTestedSocket == app.desktopSocketPath.trimmingCharacters(in: .whitespacesAndNewlines)
        let codex = EcosystemStatus.probe(connected: codexMatches ? app.connectionVerified : nil,
                                          checkedAt: codexMatches ? app.connectionTestedAt : nil)
        let quick = EcosystemStatus.probe(connected: desktopMatches ? app.desktopConnectionVerified : nil,
                                          checkedAt: desktopMatches ? app.desktopConnectionTestedAt : nil)
        let routes = app.desktopConnection["routes"] as? [String: Any] ?? [:]
        let engines = app.scheduler["engines"] as? [[String: Any]] ?? []
        let codexCLI = engines.contains { $0["id"] as? String == "codex-cli" && $0["available"] as? Bool == true }
        let harnessCLI = engines.contains { $0["id"] as? String == "harness-cli" && $0["available"] as? Bool == true }
        let cdp = EcosystemStatus.cdp(routes["cdp_web"] as? [String: Any] ?? [:], checkedAt: app.desktopRoutesCheckedAt)
        return VStack(alignment: .leading, spacing: 18) {
            Surface {
                VStack(alignment: .leading, spacing: 16) {
                    Text("Les couleurs concernent l’observation indiquée : elles ne prouvent ni le chargement des outils par un client, ni la réussite d’une tâche.")
                        .font(.caption).foregroundStyle(.secondary)
                    HStack(alignment: .center, spacing: 0) {
                        VStack(spacing: 8) {
                            Image(systemName: "arrow.clockwise.circle.fill").font(.title)
                            Text("Reprise").font(.headline)
                        }
                        .frame(width: 84).padding(.vertical, 14)
                        .background(Color.accentColor.opacity(0.08), in: RoundedRectangle(cornerRadius: 12))
                        .accessibilityElement(children: .combine)
                        Rectangle().fill(Color.secondary.opacity(0.35)).frame(width: 24, height: 2).accessibilityHidden(true)
                        VStack(alignment: .leading, spacing: 18) {
                    if app.scheduler["installed"] as? Bool == true {
                        ecosystemLink("Planificateur", symbol: "clock.arrow.circlepath", value: EcosystemStatus.scheduler(app.scheduler)) { app.resumeFilter = "À examiner" }
                    }
                    if EcosystemStatus.present(installed: applicationPresent("com.openai.codex"), detected: codexCLI || codex.level == .ready) {
                        ecosystemLink("Codex", symbol: "terminal", value: codex) { app.connectionTab = "codex" }
                    }
                    if EcosystemStatus.present(installed: FileManager.default.fileExists(atPath: app.root.appendingPathComponent("claude_desktop_quick.py").path), detected: detectedSocket(app.desktopConnection)) {
                        ecosystemLink("Quick Apply · adaptateur", symbol: "bolt.horizontal", value: quick) { app.connectionTab = "quickapply" }
                    }
                    if applicationPresent("com.anthropic.claudefordesktop") {
                        ecosystemLink("Claude Desktop", symbol: "bubble.left.and.bubble.right", value: .init(level: .attention, label: app.running.contains("com.anthropic.claudefordesktop") ? "Application ouverte" : "Application présente", detail: "La connexion du relais et le chargement des outils ne sont pas confirmés.")) { app.connectionTab = "quickapply" }
                    }
                    if FileManager.default.fileExists(atPath: app.root.appendingPathComponent("claude_cdp_pipe.py").path), (routes["cdp_web"] as? [String: Any])?["enabled"] as? Bool == true {
                        ecosystemLink("CDP web · via Quick Apply", symbol: "globe", value: cdp) { app.connectionTab = "quickapply" }
                    }
                    if EcosystemStatus.present(installed: applicationPresent("com.deepseek.dsh"), detected: harnessCLI) {
                        ecosystemLink("Harness · outils MCP", symbol: "hammer", value: .init(level: .attention, label: "Client non vérifié", detail: "L’installation ou l’ouverture de Harness ne confirme pas le chargement des outils.")) { app.verifyEverything() }
                    }
                    if applicationPresent("com.zhipuai.autoclaw") {
                        ecosystemLink("AutoClaw", symbol: "bolt", value: .init(level: .attention, label: "Présent · lien non vérifié", detail: "La présence de l’application ne confirme pas un échange avec Reprise.")) { app.verifyEverything() }
                    }
                    if ironclawConfigured && FileManager.default.fileExists(atPath: app.root.appendingPathComponent("ironclaw_connection.py").path) {
                        ecosystemLink("IronClaw · adaptateur local", symbol: "network", value: .init(level: .attention, label: "Distant non vérifié", detail: "L’adaptateur et sa configuration sont présents ; le service distant n’est pas confirmé.")) { app.connectionTab = "ironclaw" }
                    }
                    if FileManager.default.fileExists(atPath: app.root.appendingPathComponent("apple_pilot_service.py").path) && FileManager.default.fileExists(atPath: "/System/Library/Frameworks/FoundationModels.framework") {
                        ecosystemLink("Pilote Apple · adaptateur", symbol: "sparkles", value: .init(level: .attention, label: applePilot.sessionOpen ? "Session ouverte" : "Session non ouverte", detail: "La disponibilité du modèle et le résultat d’une analyse sont vérifiés séparément.")) { app.page = "apple-pilot" }
                    }
                        }
                        .overlay(alignment: .leading) {
                            Rectangle().fill(Color.secondary.opacity(0.35)).frame(width: 1)
                                .padding(.vertical, 20).accessibilityHidden(true)
                        }
                    }
                }
            }
            Text("Vert : observation confirmée · Orange : attente ou preuve manquante · Rouge : erreur observée. Les tests de connexion ne lancent pas de modèle.")
                .font(.caption).foregroundStyle(.secondary)
        }
    }

    var overview: some View {
        VStack(alignment: .leading, spacing: 20) {
            if app.lastRefresh == nil && app.busy {
                Surface { InlineStatus(busy: true, message: "Lecture de l’activité locale…") }
            } else {
                resumptionStatus
                Surface {
                    VStack(alignment: .leading, spacing: 16) {
                        HStack {
                            Text("Applications ouvertes").font(.headline)
                            Spacer()
                            Text("Activité estimée").font(.caption).foregroundStyle(.secondary)
                        }
                        if app.counters.isEmpty { Text("Aucune application suivie ouverte.").foregroundStyle(.secondary) }
                        ForEach(app.counters, id: \.0) { entry in
                            let activityText = entry.3.map { $0 > 0 ? "\($0) en cours" : "au repos" } ?? "état indisponible"
                            let accessibilityText = entry.0 + " : " + activityText
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
                            }.accessibilityElement(children: .combine).accessibilityLabel(accessibilityText)
                        }
                    }
                }
                Surface {
                    VStack(alignment: .leading, spacing: 18) {
                        HStack {
                            Text("Quotas Codex").font(.headline)
                            Spacer()
                            Button(app.quotaBusy ? "Lecture…" : "Lire les quotas") { app.refreshQuotas() }.disabled(app.quotaBusy)
                        }
                        let knowledge = app.quotaKnowledge
                        let freshness = knowledge["freshness"] as? String ?? "unobserved"
                        Text(freshness == "recent_read" ? "Lecture récente · aucune reprise déduite" : "Jauge indisponible · dernière lecture absente ou périmée").font(.caption).foregroundStyle(.secondary)
                        if let stamp = knowledge["observed_at"] as? Double { Text("Lu le " + localDateTime(Date(timeIntervalSince1970: stamp))).font(.caption).foregroundStyle(.secondary) }
                        if let pools = knowledge["pools"] as? [[String: Any]], !pools.isEmpty {
                            ForEach(Array(pools.enumerated()), id: \.offset) { _, pool in
                                VStack(alignment: .leading, spacing: 8) {
                                    Text("Offre : " + (pool["limit_id"] as? String ?? "inconnue")).font(.callout.weight(.semibold))
                                    if let windows = pool["windows"] as? [[String: Any]] {
                                        HStack(alignment: .top, spacing: 28) {
                                            ForEach(Array(windows.enumerated()), id: \.offset) { _, window in
                                                quotaRow(window["name"] as? String == "secondary" ? "Fenêtre secondaire" : "Fenêtre principale", value: window, attributed: pool["quota_pool_key"] as? String != nil)
                                            }
                                        }
                                    }
                                }
                            }
                        } else { Text("Aucune fenêtre fournisseur connue pour cette route.").foregroundStyle(.secondary) }
                        if let quota = app.snapshot["codex_quota"] as? [String: Any], let rates = quota["rates"] as? [String: Any] {
                            DisclosureGroup("Dernier signal de session · compte non attribué") {
                                HStack(alignment: .top, spacing: 28) {
                                    quotaRow("Fenêtre principale", value: rates["primary"] as? [String: Any])
                                    quotaRow("Fenêtre secondaire", value: rates["secondary"] as? [String: Any])
                                }
                                if let stamp = quota["measured_at"] as? String { Text("Événement du " + friendlyDate(stamp)).font(.caption).foregroundStyle(.secondary) }
                            }
                        }
                        Divider()
                        VStack(alignment: .leading, spacing: 10) {
                            Text("Quotas Claude").font(.headline)
                            if let quotaState = app.scheduler["claude_quota_state"] as? [String: Any], let stateStatus = quotaState["status"] as? String, !stateStatus.isEmpty {
                                let stateLabel = { () -> String in
                                    switch stateStatus {
                                    case "ok": return "État : aucun blocage de quota"
                                    case "blocked": return "État : quota bloqué"
                                    default: return "État : incertain"
                                    }
                                }()
                                Text(stateLabel).font(.callout.weight(stateStatus == "blocked" ? .bold : .semibold)).foregroundStyle(stateStatus == "blocked" ? Color.orange : .primary)
                                if let resumeAt = quotaState["resume_at"] as? Double, resumeAt.isFinite, resumeAt > 0 {
                                    Text("Quota rétabli à " + localDateTime(Date(timeIntervalSince1970: resumeAt))).font(.caption).foregroundStyle(.secondary)
                                }
                                if let checkedAt = quotaState["checked_at"] as? Double, checkedAt.isFinite, checkedAt > 0 {
                                    Text("Dernier contrôle : " + localDateTime(Date(timeIntervalSince1970: checkedAt))).font(.caption).foregroundStyle(.secondary)
                                }
                                Divider()
                            }
                            if let claudeQuota = app.scheduler["claude_quota"] as? [String: Any] {
                                let enabled = claudeQuota["enabled"] as? Bool == true
                                    && app.scheduler["auto_enabled"] as? Bool == true
                                Text(enabled ? "Reprise automatique Claude activée" : "Reprise automatique Claude en pause").font(.caption).foregroundStyle(.secondary)
                                let events = claudeQuota["events"] as? [[String: Any]] ?? []
                                if events.isEmpty {
                                    Text("Aucune reprise Claude enregistrée.").foregroundStyle(.secondary)
                                } else {
                                    let recent = events.prefix(8).compactMap { event -> (String, [String: Any])? in
                                        (event["event_key"] as? String).map { ($0, event) }
                                    }
                                ForEach(recent, id: \.0) { _, event in
                                        let status = event["status"] as? String ?? "inconnu"
                                        HStack {
                                            Text(quotaStatusLabel(status)).font(.callout.weight(status == "uncertain" ? .bold : .semibold)).foregroundStyle(status == "uncertain" ? Color.orange : .primary)
                                            Text(String((event["chat_id"] as? String ?? "").prefix(8))).font(.callout).foregroundStyle(.secondary)
                                            Spacer()
                                            if let due = event["due"] as? Double, due.isFinite, due > 0 { Text("Échéance : " + localDateTime(Date(timeIntervalSince1970: due))).font(.caption).foregroundStyle(.secondary) }
                                        }.accessibilityElement(children: .combine)
                                    }
                                    if events.count > 8 { Text("+\(events.count - 8) autres reprises").font(.caption).foregroundStyle(.secondary) }
                                }
                            } else {
                                Text("Données indisponibles.").foregroundStyle(.secondary)
                            }
                        }
                    }
                }
                claudeSessionsCard
                Surface {
                    HStack(spacing: 16) {
                        Image(systemName: "clock.arrow.circlepath").font(.title2).foregroundStyle(.secondary).accessibilityHidden(true)
                        VStack(alignment: .leading, spacing: 5) {
                            let count = app.resumeSummary["review_count"] as? Int ?? 0
                            Text(count > 0 ? "\(count) tentatives à examiner" : "Historique des reprises").font(.headline)
                            let causes = app.resumeSummary["cause_count"] as? Int ?? 0
                            Text(count > 0 ? "\(causes) causes regroupées · aucune nouvelle tentative automatique pour ces incidents." : "Aucune tentative nécessitant un examen.").font(.callout).foregroundStyle(.secondary)
                        }
                        Spacer()
                        Button("Comprendre") { app.page = "sessions"; app.resumeFilter = "À examiner" }
                    }
                }
                HStack {
                    Text("\(app.attention.count) signalements récents dans les sessions").foregroundStyle(.secondary).accessibilityLabel("\(app.attention.count) signalements récents dans les sessions")
                    Spacer()
                    Button("Voir les sessions") { app.page = "sessions"; app.sessionFilter = "Récentes" }.buttonStyle(.link)
                }.font(.callout)
                HStack(spacing: 8) {
                    Image(systemName: "info.circle").accessibilityHidden(true)
                    Text("Un agent silencieux peut rester actif. Les estimations reflètent les deux dernières minutes.")
                }.font(.caption).foregroundStyle(.secondary)
            }
        }
    }
    var resumptionStatus: some View {
        Surface {
            VStack(alignment: .leading, spacing: 12) {
                HStack {
                    Label("Reprise après quota", systemImage: "arrow.clockwise.circle").font(.headline)
                    Spacer()
                    Toggle("Quota Codex", isOn: Binding(get: { app.scheduler["auto_enabled"] as? Bool ?? false }, set: { app.setAutomatic($0) }))
                        .toggleStyle(.switch).disabled(app.scheduler.isEmpty)
                }
                if app.scheduler.isEmpty {
                    Text("État du service indisponible").foregroundStyle(.secondary)
                } else if app.scheduler["installed"] as? Bool != true || app.scheduler["executable_available"] as? Bool != true {
                    Label("⚠ Service de reprise indisponible", systemImage: "exclamationmark.triangle").foregroundStyle(.orange)
                } else if (app.scheduler["running"] as? Int ?? 0) + (app.scheduler["launching"] as? Int ?? 0) == 0 && Date().timeIntervalSince1970 - (app.scheduler["last_tick"] as? Double ?? 0) > 180 {
                    Label("⚠ Dernière vérification du service trop ancienne", systemImage: "exclamationmark.triangle").foregroundStyle(.orange)
                } else if let error = app.scheduler["last_error"] as? String, !error.isEmpty {
                    Label("⚠ " + error, systemImage: "exclamationmark.triangle").foregroundStyle(.orange)
                } else {
                    let running = app.scheduler["running"] as? Int ?? 0
                    let launching = app.scheduler["launching"] as? Int ?? 0
                    let pending = app.scheduler["pending"] as? Int ?? 0
                    Text(running > 0 ? (running == 1 ? "1 tour démarré" : "\(running) tours démarrés") : launching > 0 ? "Connexion au moteur · démarrage non confirmé" : pending > 0 ? (pending == 1 ? "1 reprise en attente" : "\(pending) reprises en attente") : "Aucune reprise en attente").font(.title3.weight(.semibold))
                    Text(app.scheduler["auto_enabled"] as? Bool == true ? "Surveillance automatique activée" : "Automatisation en pause").foregroundStyle(.secondary)
                    if let due = app.scheduler["next_due"] as? Double {
                        Text("Prochaine reprise Codex : " + localDateTime(Date(timeIntervalSince1970: due))).font(.caption).foregroundStyle(.secondary)
                    }
                    if let quotaState = app.scheduler["claude_quota_state"] as? [String: Any],
                       let resumeAt = quotaState["resume_at"] as? Double, resumeAt.isFinite, resumeAt > 0 {
                        Text("Quota Claude rétabli à " + localDateTime(Date(timeIntervalSince1970: resumeAt))).font(.caption).foregroundStyle(.secondary)
                    }
                    if pending + running + launching > 0 {
                        Button("Voir les reprises") { app.page = "sessions"; app.resumeFilter = "En attente" }.buttonStyle(.link)
                    }
                }
            }
        }
    }
    var resumptions: some View {
        VStack(alignment: .leading, spacing: 18) {
            resumptionStatus
            Picker("Afficher les reprises", selection: $app.resumeFilter) {
                ForEach(["À examiner", "En attente", "Historique"], id: \.self) { Text($0) }
            }.pickerStyle(.segmented).frame(maxWidth: 540)
            if app.resumeFilter == "À examiner" {
                let groups = app.resumeSummary["groups"] as? [[String: Any]] ?? []
                if groups.isEmpty { empty("Aucune tentative à examiner", icon: "checkmark.circle", detail: "Les prochaines reprises et les résultats précédents restent accessibles dans les autres vues.") }
                ForEach(Array(groups.enumerated()), id: \.offset) { _, group in
                    Surface {
                        VStack(alignment: .leading, spacing: 12) {
                            HStack(alignment: .firstTextBaseline) {
                                Text(group["title"] as? String ?? "Résultat à vérifier").font(.headline)
                                Spacer()
                                Text("\(group["count"] as? Int ?? 0) " + ((group["count"] as? Int ?? 0) > 1 ? "tentatives" : "tentative")).font(.callout.monospacedDigit()).foregroundStyle(.secondary)
                            }
                            Text(group["action"] as? String ?? "").font(.callout).foregroundStyle(.secondary)
                            if let stamp = group["last_at"] as? Double { Text("Dernière tentative : " + localDateTime(Date(timeIntervalSince1970: stamp))).font(.caption).foregroundStyle(.secondary) }
                            DisclosureGroup("Voir les sessions concernées") {
                                let ids = group["task_ids"] as? [String] ?? []
                                ForEach(Array(app.resumeTasks.filter { ids.contains($0["id"] as? String ?? "") }.enumerated()), id: \.offset) { _, task in
                                    Divider().padding(.vertical, 6)
                                    attemptDetail(task)
                                }
                            }.font(.callout)
                        }
                    }
                }
            } else {
                let pending = ["ready", "waiting_quota", "running", "launching"]
                let tasks = app.resumeTasks.filter { app.resumeFilter == "En attente" ? pending.contains($0["status"] as? String ?? "") : !pending.contains($0["status"] as? String ?? "") }
                if tasks.isEmpty { empty(app.resumeFilter == "En attente" ? "Aucune reprise en attente" : "Aucune tentative enregistrée", icon: "clock", detail: "Les tentatives précédentes ne déclenchent aucune nouvelle relance à elles seules.") }
                ForEach(Array(tasks.enumerated()), id: \.offset) { _, task in Surface { attemptDetail(task) } }
            }
            Text("Historique des tentatives de Reprise. Les journaux sont conservés.").font(.caption).foregroundStyle(.secondary)
        }
    }
    func attemptDetail(_ task: [String: Any]) -> some View {
        VStack(alignment: .leading, spacing: 7) {
            Text((task["cwd"] as? String).map { URL(fileURLWithPath: $0).lastPathComponent } ?? "Session").font(.callout.weight(.semibold))
            Text(task["label"] as? String ?? "État inconnu").font(.callout)
            if let reason = task["reason"] as? String { Text(reason).font(.caption).foregroundStyle(.secondary).textSelection(.enabled) }
            Text((task["engine_label"] as? String ?? "") + " · " + String((task["session"] as? String ?? "").suffix(8))).font(.caption).foregroundStyle(.secondary)
            if let stamp = task["ended_at"] as? Double ?? task["due"] as? Double { Text(localDateTime(Date(timeIntervalSince1970: stamp))).font(.caption).foregroundStyle(.secondary) }
            let source = app.rows.first { ($0["id"] as? String) == (task["session"] as? String) && ($0["provider"] as? String) == (task["provider"] as? String) }
            let parent = source?["parent_session"] as? String
            Button(parent == nil ? "Ouvrir la session" : "Ouvrir la session parente") {
                app.openSession(["provider": task["provider"] as? String ?? "Codex", "id": parent ?? task["session"] as? String ?? ""])
            }.buttonStyle(.link)
        }.frame(maxWidth: .infinity, alignment: .leading)
    }
    /// Amendement #263 : jauge par fournisseur et par harness — une ligne par
    /// couple, avec dégradation gracieuse quand aucune jauge fiable n’existe.
    func quotaGaugeRow(_ name: String, symbol: String, percent: Double?, note: String) -> some View {
        HStack(spacing: 14) {
            Label(name, systemImage: symbol).font(.callout.weight(.semibold)).frame(width: 210, alignment: .leading)
            if let percent {
                VStack(alignment: .leading, spacing: 3) {
                    Text("\(Int(percent)) % restants").font(.callout.monospacedDigit())
                    ProgressView(value: percent, total: 100)
                        .tint(percent < 15 ? .orange : .accentColor)
                        .frame(width: 180)
                }
            }
            Spacer()
            Text(note).font(percent == nil ? .callout : .caption).foregroundStyle(.secondary).lineLimit(2)
        }
        .accessibilityElement(children: .combine)
    }
    var quotaGauges: some View {
        let codexFresh = app.quotaKnowledge["freshness"] as? String ?? "unobserved"
        let codexPools = (app.quotaKnowledge["pools"] as? [[String: Any]] ?? []).flatMap { pool in
            (pool["windows"] as? [[String: Any]] ?? []).compactMap { window -> (Double, String)? in
                guard let used = window["used_percent"] as? Double, used.isFinite, (0...100).contains(used) else { return nil }
                let minutes = (window["window_minutes"] ?? window["duration_minutes"]) as? Int ?? 0
                let label = minutes == 300 ? "Codex CLI · fenêtre de 5 heures" : minutes == 10080 ? "Codex CLI · fenêtre hebdomadaire" : "Codex CLI · fenêtre rapportée"
                return (100 - used, label)
            }
        }
        let claudeState = (app.scheduler["claude_quota_state"] as? [String: Any])?["status"] as? String ?? ""
        return Surface {
            VStack(alignment: .leading, spacing: 14) {
                HStack {
                    Text("Jauges de quota").font(.headline)
                    Spacer()
                    Button(app.quotaBusy ? "Lecture…" : "Actualiser les jauges") { app.refreshQuotas() }.disabled(app.quotaBusy)
                }
                if codexPools.isEmpty {
                    quotaGaugeRow("Codex CLI", symbol: "terminal", percent: nil,
                                  note: codexFresh == "recent_read" ? "Lecture récente sans fenêtre chiffrée" : "Pas de jauge fiable")
                } else {
                    ForEach(Array(codexPools.enumerated()), id: \.offset) { _, gauge in
                        quotaGaugeRow(gauge.1, symbol: "terminal", percent: gauge.0,
                                      note: codexFresh == "recent_read" ? "Lecture récente" : "Lecture à rafraîchir")
                    }
                }
                quotaGaugeRow("Claude Desktop", symbol: "bubble.left.and.bubble.right", percent: nil,
                              note: claudeState == "ok" ? "Aucun blocage de quota observé"
                                : claudeState == "blocked" ? "Quota bloqué · rétablissement annoncé"
                                : "Pas de jauge fiable")
                quotaGaugeRow("ChatGPT Desktop", symbol: "globe", percent: nil, note: "Pas de jauge fiable")
                quotaGaugeRow("DeepSeek et autres fournisseurs", symbol: "shippingbox", percent: nil, note: "Pas de jauge fiable")
                quotaGaugeRow("Pilote Apple", symbol: "sparkles", percent: nil, note: "Sans quota · exécution sur l’appareil")
                Text("Une jauge chiffrée provient d’une lecture locale de l’application concernée ; elle ne prouve pas l’état côté fournisseur.")
                    .font(.caption).foregroundStyle(.secondary)
            }
        }
    }
    func quotaRow(_ name: String, value: [String: Any]?, attributed: Bool = false) -> some View {
        VStack(alignment: .leading, spacing: 7) {
            if let value {
                let used = value["used_percent"] as? Double
                let validUsed = used.flatMap { $0.isFinite && (0...100).contains($0) ? $0 : nil }
                let remaining = validUsed.map { 100 - $0 }
                let minutes = (value["duration_minutes"] ?? value["window_minutes"]) as? Int
                let title = minutes == 300 ? "Fenêtre de 5 heures" : minutes == 10080 ? "Fenêtre hebdomadaire" : name
                let reset = value["resets_at"] as? Double
                let date = reset.flatMap { $0.isFinite && $0 > 0 ? Date(timeIntervalSince1970: $0) : nil }
                let expired = date.map { $0 <= Date() } ?? false
                Text(title).font(.callout.weight(.semibold))
                if let minutes, minutes > 0 { Text("Fenêtre observée : \(minutes) min").font(.caption).foregroundStyle(.secondary) }
                if expired {
                    Text("Mesure périmée").font(.system(size: 27, weight: .semibold, design: .rounded)).foregroundStyle(.secondary)
                        .accessibilityLabel(title + " : reset dépassé, quota actuel inconnu")
                } else if let remaining {
                    Text("\(Int(remaining)) % observés restants" + (remaining < 15 ? " ⚠" : "")).font(.system(size: 27, weight: .semibold, design: .rounded)).monospacedDigit()
                        .accessibilityLabel(title + " : " + String(Int(remaining)) + " % observés restants")
                    ProgressView(value: remaining, total: 100).tint(remaining < 15 ? .orange : .accentColor)
                } else {
                    Text("Quantité inconnue").font(.callout).foregroundStyle(.secondary)
                }
                Text(attributed ? "Compte associé à cette observation" : "Compte non attribué").font(.caption).foregroundStyle(.secondary)
                if let date {
                    Label("Reset déclaré : " + localDateTime(date), systemImage: "clock").font(.caption).foregroundStyle(.secondary)
                } else {
                    Text("Date de reset inconnue").font(.caption).foregroundStyle(.secondary)
                }
            } else {
                Text(name + " · indisponible").foregroundStyle(.secondary)
            }
        }
    }
    var claudeSessionsCard: some View {
        let status = app.claudeSessionsStatus
        let route = status["route"] as? [String: Any] ?? [:]
        let sessions = status["sessions"] as? [[String: Any]] ?? []
        let enabled = route["enabled"] as? Bool == true
        let rootStatus = status["status"] as? String ?? "unavailable"
        let statusKnown = rootStatus == "ok"
        let lastRead = (route["last_refresh_at"] as? Double).flatMap { $0.isFinite && $0 > 0 ? Date(timeIntervalSince1970: $0) : nil }
        return Surface {
            VStack(alignment: .leading, spacing: 12) {
                HStack {
                    Text("Sessions Claude Desktop · observation").font(.headline)
                    Spacer()
                    Button("Configurer") { app.connectionTab = "quickapply" }.buttonStyle(.link)
                }
                Toggle("Activer l’observation (opt-in)", isOn: $app.claudeObserverEnabledDraft)
                    .disabled(app.claudeSessionsBusy)
                HStack(spacing: 12) {
                    Text("Intervalle (s)").font(.caption).foregroundStyle(.secondary)
                    TextField("60", text: $app.claudeObserverInterval).frame(width: 90).textFieldStyle(.roundedBorder)
                    Button("Enregistrer") { app.configureClaudeObserver(enabled: app.claudeObserverEnabledDraft) }
                        .disabled(app.claudeSessionsBusy)
                    Button("Actualiser") { app.refreshClaudeSessions() }.disabled(app.claudeSessionsBusy)
                    if app.claudeSessionsBusy { ProgressView().controlSize(.small) }
                }
                if !app.claudeSessionsNote.isEmpty { InlineStatus(busy: false, message: app.claudeSessionsNote) }
                if app.observerStartupFailed {
                    Button("Réessayer l’activation de l’observation") { app.retryObserverStartup() }.disabled(app.busy)
                }
                Text(!statusKnown ? "État de configuration inconnu · \(rootStatus)" : enabled ? "Observation CDP opt-in · sessions ouvertes seulement" : "Observation désactivée · aucune lecture CDP").font(.caption).foregroundStyle(.secondary)
                SettingsHint(
                    text: "Une session associée ne prouve ni l’admission du message ni le retour MCP.",
                    detail: "L’association est déclarée par vous. L’observation est limitée aux onglets ouverts et reste en lecture seule. État de la route : "
                        + (route["refresh_status"] as? String ?? "inconnu")
                        + " · instance : " + (route["instance_status"] as? String ?? "inconnue")
                        + " · couverture : " + (route["coverage"] as? String ?? "onglets ouverts uniquement")
                        + (lastRead.map { "\nDernière lecture : " + localDateTime($0) } ?? "")
                        + ((route["next_refresh_at"] as? Double).flatMap { $0.isFinite && $0 > 0 ? "\nProchaine observation au plus tôt : " + localDateTime(Date(timeIntervalSince1970: $0)) : nil } ?? ""),
                    detailLabel: "État détaillé")
                Divider()
                Text("Associer un chat déjà ouvert").font(.callout.weight(.semibold))
                TextField("Identifiant du chat (UUID)", text: $app.claudeSessionChatID).textFieldStyle(.roundedBorder)
                Toggle("Je confirme que ce chat est ouvert dans Claude Desktop", isOn: $app.claudeSessionConfirmed)
                    .disabled(app.claudeSessionsBusy)
                HStack(spacing: 10) {
                    Button("Associer") { app.registerClaudeSession() }
                        .disabled(app.claudeSessionsBusy || !app.claudeSessionConfirmed)
                    Button("Retirer l’association") { app.registerClaudeSession(remove: true) }
                        .disabled(app.claudeSessionsBusy)
                }
                if sessions.isEmpty {
                    Text(statusKnown ? "Aucune session Desktop déclarée. L’observation ne crée pas de chat." : "Sessions inconnues tant que l’état du connecteur n’est pas disponible.").font(.callout).foregroundStyle(.secondary)
                }
                ForEach(Array(sessions.enumerated()), id: \.offset) { _, session in
                    claudeSessionSummary(session)
                }
                if (status["capabilities"] as? [String: Any])?["action_eligible"] as? Bool != true {
                    SettingsHint(
                        text: "Aucune reprise n’est proposée depuis cet écran.",
                        detail: "La pilotabilité, l’admission du message et le retour MCP sont trois capacités distinctes. L’observation ne les prouve pas ; une reprise n’est donc proposée que lorsqu’elles sont toutes vérifiées.")
                }
            }
        }
    }
    @ViewBuilder func claudeSessionSummary(_ session: [String: Any]) -> some View {
        let registration = session["registration"] as? [String: Any] ?? [:]
        let observation = session["observation"] as? [String: Any] ?? [:]
        let quota = session["quota"] as? [String: Any] ?? [:]
        let presence = observation["presence"] as? String ?? "unavailable"
        VStack(alignment: .leading, spacing: 6) {
            HStack {
                Text("Claude Desktop · " + String((session["chat_id"] as? String ?? "session").suffix(8))).font(.callout.weight(.semibold))
                Spacer()
                Text(presence == "open" ? "Ouvert observé" : presence == "not_open" ? "Non ouvert" : "Présence inconnue").font(.caption).foregroundStyle(.secondary)
            }
            if let lastSeen = observation["last_seen_at"] as? Double, lastSeen.isFinite, lastSeen > 0 {
                let targetCount = observation["target_count"] as? Int ?? 0
                Text("Présence observée le \(localDateTime(Date(timeIntervalSince1970: lastSeen))) · \(targetCount == 1 ? "1 onglet cible" : "\(targetCount) onglets cibles")").font(.caption).foregroundStyle(.secondary)
            }
            let knowledge = quota["knowledge"] as? String ?? "unknown"
            let blocked = quota["blocked"] as? Bool
            Text(blocked == true ? "Blocage signalé · compte ou pool non attribué" : knowledge == "unknown" ? "Quota inconnu" : "État de quota : \(knowledge)").font(.callout)
            if let observed = quota["observed_at"] as? Double, observed.isFinite, observed > 0 {
                Text("Observation quota : \(localDateTime(Date(timeIntervalSince1970: observed))) · \(quota["freshness"] as? String ?? "fraîcheur inconnue")").font(.caption).foregroundStyle(.secondary)
            } else {
                Text("Aucune observation quota récente · \(quota["observation_status"] as? String ?? "indisponible")").font(.caption).foregroundStyle(.secondary)
            }
            Text("Source : \(quota["source"] as? String ?? "inconnue") · compte non attribué").font(.caption).foregroundStyle(.secondary)
            if let pools = quota["pools"] as? [[String: Any]] {
                ForEach(Array(pools.enumerated()), id: \.offset) { _, pool in
                    if let windows = pool["windows"] as? [[String: Any]] {
                        ForEach(Array(windows.enumerated()), id: \.offset) { _, window in
                            quotaRow(window["name"] as? String ?? "Fenêtre rapportée", value: window)
                        }
                    }
                }
            }
            let datedReset = ((quota["pools"] as? [[String: Any]]) ?? []).contains { pool in
                ((pool["windows"] as? [[String: Any]]) ?? []).contains { window in
                    guard let reset = window["resets_at"] as? Double else { return false }
                    return reset.isFinite && reset > 0
                }
            }
            if !datedReset, let displayed = quota["displayed_reset_time"] as? String, !displayed.isEmpty {
                Text("Heure affichée : \(displayed) · date inconnue (\(quota["reset_status"] as? String ?? "ambiguë"))").font(.caption).foregroundStyle(.secondary)
            }
            if let historical = quota["historical_reset"] as? [String: Any], let reset = historical["resets_at"] as? Double, reset.isFinite, reset > 0 {
                Text("Dernier reset historique (pas un état courant) : \(localDateTime(Date(timeIntervalSince1970: reset)))").font(.caption).foregroundStyle(.secondary)
            }
            Text("Association déclarative : \(registration["source"] as? String ?? "inconnue") · non attestée · observation CDP ≠ admission ni retour MCP.").font(.caption).foregroundStyle(.secondary)
        }
        .padding(.top, 8)
    }
    var sessions: some View {
        VStack(alignment: .leading, spacing: 16) {
            Picker("Filtrer", selection: $app.sessionFilter) { ForEach(["Récentes", "Toutes", "Erreurs", "Arrêts", "À examiner"], id: \.self) { Text($0) } }.pickerStyle(.segmented).frame(maxWidth: 460)
            let visible = app.rows.filter { row in
                let state = row["state"] as? String ?? ""
                return (app.sessionFilter == "Récentes" && app.attention.contains { ($0["id"] as? String) == (row["id"] as? String) }) || app.sessionFilter == "Toutes" || (app.sessionFilter == "Erreurs" && ["failed", "quota_candidate", "quota_confirmed", "rate_limited"].contains(state)) || (app.sessionFilter == "Arrêts" && state == "stopped") || (app.sessionFilter == "À examiner" && ["unfinished", "needs_user"].contains(state))
            }
            if visible.isEmpty { empty("Aucun signalement dans cette vue", icon: "tray", detail: app.sessionFilter == "Récentes" ? "Aucune interruption détectée sur les dernières 24 heures. Consultez Toutes pour l’historique." : "Essayez un autre filtre pour consulter les autres sessions.") }
            ForEach(Array(visible.enumerated()), id: \.offset) { _, row in
                Surface {
                    DisclosureGroup {
                        VStack(alignment: .leading, spacing: 10) {
                            Text(row["label"] as? String ?? "À vérifier")
                            if let stamp = row["updated"] as? Double { Text(localDateTime(Date(timeIntervalSince1970: stamp))).foregroundStyle(.secondary) }
                            if let tokens = row["tokens"] as? Double { Text("Tokens enregistrés : \(Int(min(tokens, 1e15)))") }
                            if let cost = row["cost_usd"] as? Double, cost > 0 { Text(String(format: "Estimation enregistrée : %.4f $", cost)) }
                            Text(row["id"] as? String ?? "").font(.caption.monospaced()).foregroundStyle(.secondary).textSelection(.enabled)
                            Text("Aucune reprise automatique sur la seule base de ce signalement.").font(.caption).foregroundStyle(.secondary)
                        }.padding(.top, 12)
                    } label: {
                        Button { app.openSession(row) } label: {
                        HStack(spacing: 12) {
                            SessionStateMark(stopped: row["state"] as? String == "stopped")
                            VStack(alignment: .leading, spacing: 4) { Text(sessionTitle(row)).font(.headline); Text((row["provider"] as? String ?? "") + " · " + ((row["updated"] as? Double).map { localDateTime(Date(timeIntervalSince1970: $0)) } ?? "Date inconnue")).font(.caption).foregroundStyle(.secondary) }
                            Spacer(); Text(row["state"] as? String == "stopped" ? "⏸ Arrêtée" : "⚠ À vérifier").font(.callout.weight(.medium)).accessibilityLabel(row["state"] as? String == "stopped" ? "Arrêtée" : "À vérifier")
                        }
                        }.buttonStyle(.plain).help("Afficher l’application de cette session")
                    }
                }
            }
            Text("Les arrêts volontaires sont séparés des échecs. Cette liste conserve jusqu’à 20 sessions récentes.").font(.caption).foregroundStyle(.secondary)
        }
    }
    var analysis: some View {
        VStack(alignment: .leading, spacing: 18) {
            Surface { HStack { VStack(alignment: .leading, spacing: 6) { Label("Apple Intelligence", systemImage: "sparkles").font(.headline); Text(app.analysis["status"] as? String ?? "En attente").font(.callout).foregroundStyle(.secondary).accessibilityLabel("Statut de l'analyse : " + (app.analysis["status"] as? String ?? "En attente")) }; Spacer(); HStack(spacing: 8) { Button(app.analysisBusy ? "Analyse…" : "Analyser") { app.analyzeLocal() }.disabled(app.analysisBusy); if app.analysisBusy { InlineStatus(busy: true, message: "Synthèse en cours…") } } } }
            Surface {
                VStack(alignment: .leading, spacing: 12) {
                    Label("Synthèse locale (Apple Intelligence, sur l’appareil)", systemImage: "sparkles").font(.headline)
                    if app.localSynthesis["available"] as? Bool == true {
                        Text("Mises à jour, relations entre Harness et dernières activités, d’après les journaux locaux.").font(.callout).foregroundStyle(.secondary)
                        Text(app.localSynthesis["answer"] as? String ?? "").font(.callout)
                    } else {
                        Text("Modèle génératif indisponible. Les vérifications déterministes restent la référence.").font(.callout).foregroundStyle(.secondary)
                        if let reason = app.localSynthesis["reason"] as? String { Text(reason).font(.caption).foregroundStyle(.secondary).textSelection(.enabled) }
                    }
                    Text("Enrichissement facultatif, en lecture seule ; n’autorise aucune action.").font(.caption).foregroundStyle(.secondary)
                }
            }
            if app.verdicts.isEmpty { empty("Aucun signalement à afficher", icon: "text.badge.checkmark", detail: "L’absence de signalement ne garantit pas la réussite d’une session. Les vérifications du projet restent la référence.") }
            ForEach(Array(app.verdicts.enumerated()), id: \.offset) { _, value in Surface { VStack(alignment: .leading, spacing: 10) { Button { app.openSession(value) } label: { Text(value["title"] as? String ?? "Session").font(.headline) }.buttonStyle(.plain).help("Afficher l’application de cette session"); Text(value["reason"] as? String ?? ""); Text(value["evidence"] as? String ?? "").font(.callout).foregroundStyle(.secondary).textSelection(.enabled) } } }
            Text("Codex et Claude Code · un nouveau compte rendu au maximum par minute · aucune relance déclenchée par le modèle.").font(.caption).foregroundStyle(.secondary)
        }
    }
    var search: some View {
        VStack(alignment: .leading, spacing: 18) {
            HStack(spacing: 12) { Image(systemName: "magnifyingglass").foregroundStyle(.secondary); TextField("Tests Rust, quota, nom de fichier…", text: $app.query).textFieldStyle(.plain).onSubmit { app.search() }; if app.searching { InlineStatus(busy: true, message: app.searchNote) }; Button("Rechercher") { app.search() }.disabled(app.searching || app.query.trimmingCharacters(in: .whitespaces).isEmpty).buttonStyle(.borderedProminent).accessibilityLabel("Lancer la recherche") }.padding(14).background(.background, in: RoundedRectangle(cornerRadius: 14))
            Text(app.searchNote).font(.callout).foregroundStyle(.secondary)
            if app.searchResults.isEmpty && !app.searching {
                empty(app.searchHasRun ? "Aucun résultat pour cette recherche" : "Retrouvez le fil d’une session", icon: "doc.text.magnifyingglass", detail: app.searchHasRun ? "Essayez un terme plus court ou un code d’erreur exact." : "Recherchez dans les états récents et comptes rendus analysés, toutes applications confondues.")
            }
            ForEach(Array(app.searchResults.enumerated()), id: \.offset) { _, value in Surface { VStack(alignment: .leading, spacing: 8) { Button { app.openSession(value) } label: { Text(value["title"] as? String ?? "Session").font(.headline) }.buttonStyle(.plain).help("Afficher l’application de cette session"); Text((value["provider"] as? String ?? "") + " · " + String((value["id"] as? String ?? "").suffix(8))).font(.caption).foregroundStyle(.secondary); Text(value["text"] as? String ?? "").textSelection(.enabled) } } }
        }
    }
    // Single "Connexions" tab: an internal picker switches between the three
    // connection panels (codex-desktop / claude-desktop / ironclaw-ssh), freeing the sidebar.
    var connections: some View {
        VStack(alignment: .leading, spacing: 18) {
            if let verification = app.integrationVerification,
               verification["status"] as? String == "verified",
               verification["update_required"] as? Bool == true {
                SettingsBanner(ok: false, title: "Mise à jour des connecteurs nécessaire",
                               detail: "Le pont Harness manque d'outils de délégation ou sa release est périmée. Ouvrez Intégrations, préparez un plan --delegate, puis rechargez Harness.")
                Button("Relancer la vérification") { app.verifyEverything() }
            }
            Picker("Service", selection: Binding(get: { app.connectionTab }, set: { app.connectionTab = $0 })) {
                Text("codex-desktop").tag("codex")
                Text("claude-desktop").tag("quickapply")
                Text("IronClaw SSH").tag("ironclaw")
            }
            .pickerStyle(.segmented)
            .frame(maxWidth: 480)
            .accessibilityLabel("Choisir le service à configurer")
            if app.connectionTab == "codex" {
                connection
            } else if app.connectionTab == "ironclaw" {
                IronClawConnection(app: app)
            } else {
                desktopConnection
            }
        }
    }

    var connection: some View {
        let shared = app.connection["shared"] as? [String: Any] ?? [:]
        let customSocket = app.connection["mode"] as? String == "server"
        return VStack(alignment: .leading, spacing: 18) {
            SettingsBanner(
                ok: !app.connection.isEmpty,
                title: app.connection.isEmpty ? "Configuration indisponible" : "Configuration Codex chargée",
                detail: app.connection.isEmpty ? "Lancez une actualisation pour lire l’état du service."
                    : customSocket ? "Socket personnalisé enregistré. Testez la connexion pour vérifier le service." : "Détection automatique du socket local ou de la CLI ; la configuration seule ne confirme pas la connexion.")
            SettingsCard(title: "Partage des sessions avec Codex Desktop",
                         status: shared["startup_eligible"] as? Bool == true ? "Validé" : "À valider",
                         ok: shared["startup_eligible"] as? Bool == true) {
                Text("Ce parcours vérifie un chat dans le service partagé. Le transport utilisé par Desktop et la propriété de ses chats ne sont pas déduits de ce seul test.")
                    .font(.caption).foregroundStyle(.secondary)
                HStack(spacing: 10) {
                    Button("1. Préparer codexd") { app.sharedAction("prepare") }.disabled(app.sharedBusy)
                    Button("2. Démarrer ReChatGPT") { app.launchReChatGPT() }
                        .disabled(app.sharedBusy)
                    Spacer()
                    if app.sharedBusy { InlineStatus(busy: true, message: app.sharedNote) }
                }
                HStack(spacing: 10) {
                    TextField("Identifiant ou lien du chat à vérifier", text: $app.sharedSession).textFieldStyle(.roundedBorder)
                    Button("3. Vérifier le chat") { app.sharedAction("check") }.disabled(app.sharedBusy || app.sharedSession.isEmpty)
                }
                if !app.sharedNote.isEmpty { InlineStatus(busy: false, message: app.sharedNote) }
                SettingsHint(
                    text: "Après validation, choisissez le socket et réactivez les reprises depuis leur vue.",
                    detail: "La vérification confirme que le chat est chargé dans le service partagé, sans prouver que Desktop utilise ce même propriétaire. Vérifiez aussi les outils de Desktop avant d’activer le lancement automatique. Contrôlez l’état des reprises après validation.")
                Divider()
                HStack(spacing: 10) {
                    Button("Arrêter codexd") { app.sharedAction("stop") }
                        .disabled(app.sharedBusy || app.running.contains("com.openai.codex"))
                    Text("Fermez Desktop d’abord ; une session active empêche l’arrêt.").font(.caption).foregroundStyle(.secondary)
                }
            }
            SettingsCard(title: "Socket de codexd",
                         status: app.connectionVerified ? "Testé" : (customSocket ? "Personnalisé" : "Automatique"),
                         ok: app.connectionVerified || !customSocket) {
                if let path = app.connection["configured_socket"] as? String {
                    Text(path).font(.caption.monospaced()).foregroundStyle(.secondary).textSelection(.enabled)
                }
                ForEach(Array((app.connection["candidates"] as? [[String: Any]] ?? []).enumerated()), id: \.offset) { _, candidate in
                    HStack(alignment: .top, spacing: 12) {
                        SocketCandidateMark(detected: candidate["detected"] as? Bool == true)
                        VStack(alignment: .leading, spacing: 4) {
                            Text(candidate["label"] as? String ?? "Socket").font(.callout.weight(.semibold))
                            Text(candidate["path"] as? String ?? "").font(.caption.monospaced()).foregroundStyle(.secondary).textSelection(.enabled)
                        }
                        Spacer()
                        if candidate["detected"] as? Bool == true {
                            Button("Utiliser") {
                                app.socketPath = candidate["path"] as? String ?? ""
                                app.connectionVerified = false
                                app.connectionNote = "Chemin sélectionné. Testez la connexion avant de l’enregistrer."
                            }.disabled(app.connectionBusy)
                        } else {
                            Text("Indisponible").font(.caption).foregroundStyle(.secondary)
                        }
                    }
                }
                if (app.connection["candidates"] as? [[String: Any]] ?? []).isEmpty {
                    Text("Aucun socket détecté sur ce Mac.").font(.callout).foregroundStyle(.secondary)
                }
                Divider()
                TextField("Chemin absolu du socket", text: $app.socketPath).textFieldStyle(.roundedBorder).disabled(app.connectionBusy)
                HStack(spacing: 10) {
                    Button("Tester") { app.testConnection() }.disabled(app.connectionBusy || app.socketPath.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                    Button("Enregistrer") { app.testConnection(save: true) }
                        .disabled(app.connectionBusy || !app.connectionVerified || app.testedSocket != app.socketPath.trimmingCharacters(in: .whitespacesAndNewlines))
                    Spacer()
                    if app.connectionBusy { InlineStatus(busy: true, message: app.connectionNote) }
                }
                if !app.connectionNote.isEmpty { InlineStatus(busy: false, message: app.connectionNote) }
                SettingsHint(
                    text: "Un test réussi confirme le service, pas la reprise des sessions ouvertes dans Desktop.",
                    detail: "Le test initialise uniquement le protocole du socket : aucun appel au modèle, aucune reprise de session. La capacité à reprendre un chat ouvert dans Desktop dépend d’un contrôle distinct.")
                Button("Rétablir la détection automatique") { app.restoreConnectionDetection() }
                    .disabled(app.connectionBusy || !customSocket)
            }
        }
    }
    var desktopConnection: some View {
        let customSocket = app.desktopConnection["mode"] as? String == "server"
        return VStack(alignment: .leading, spacing: 18) {
            SettingsBanner(
                ok: !app.desktopConnection.isEmpty,
                title: app.desktopConnection.isEmpty ? "Configuration indisponible" : "Configuration Claude chargée",
                detail: app.desktopConnection.isEmpty ? "Lancez une actualisation pour lire l’état du service."
                    : customSocket ? "Socket personnalisé enregistré. Testez la connexion pour vérifier le service." : "Détection automatique du socket local ; la configuration seule ne confirme pas la connexion.")
            SettingsCard(title: "Service et application liée",
                         status: (app.quickApplyStartupEnabled || app.desktopStartup) ? "Activation au démarrage" : "Manuel",
                         ok: true) {
                HStack(spacing: 10) {
                    Button("Ouvrir ReClaude") { app.desktopAction("launch") }.disabled(app.desktopBusy)
                    Button("Démarrer claude-desktopd") { app.desktopAction("start") }.disabled(app.desktopBusy)
                    Button("Arrêter claude-desktopd") { app.desktopAction("stop") }.disabled(app.desktopBusy)
                    if app.desktopBusy { InlineStatus(busy: true, message: app.desktopNote) }
                }
                if !app.desktopNote.isEmpty { InlineStatus(busy: false, message: app.desktopNote) }
                Toggle("Démarrer l’application liée à l’ouverture de Reprise", isOn: Binding(get: { app.desktopStartup }, set: { app.setDesktopStartup($0) }))
                    .disabled(app.desktopBusy)
                Toggle("Démarrer claude-desktopd à l’ouverture de Reprise", isOn: Binding(get: { app.quickApplyStartupEnabled }, set: { app.setQuickApplyStartup($0) }))
                    .disabled(app.desktopBusy)
                if !app.quickApplyStartupResult.isEmpty {
                    SettingsHint(text: "Quick Apply : \(app.quickApplyStartupResult["status"] as? String ?? "état inconnu")",
                                 detail: (app.quickApplyStartupResult["message"] as? String ?? "")
                                    + "\nLe greeting du daemon ne prouve pas que le relais de l’application est connecté.")
                }
                SettingsHint(
                    text: "Le daemon peut démarrer indépendamment de ReClaude. La livraison des demandes nécessite ensuite un relais connecté.",
                    detail: "Le démarrage de l’application liée ne la ferme ni ne la relance si elle est déjà ouverte. Aucun autre élément d’ouverture macOS n’est modifié.")
            }
            SettingsCard(title: "Socket de claude-desktopd",
                         status: app.desktopConnectionVerified ? "Testé" : (customSocket ? "Personnalisé" : "Automatique"),
                         ok: app.desktopConnectionVerified || !customSocket) {
                if let path = app.desktopConnection["configured_socket"] as? String {
                    Text(path).font(.caption.monospaced()).foregroundStyle(.secondary).textSelection(.enabled)
                }
                ForEach(Array((app.desktopConnection["candidates"] as? [[String: Any]] ?? []).enumerated()), id: \.offset) { _, candidate in
                    HStack(alignment: .top, spacing: 12) {
                        SocketCandidateMark(detected: candidate["detected"] as? Bool == true)
                        VStack(alignment: .leading, spacing: 4) {
                            Text(candidate["label"] as? String ?? "Socket").font(.callout.weight(.semibold))
                            Text(candidate["path"] as? String ?? "").font(.caption.monospaced()).foregroundStyle(.secondary).textSelection(.enabled)
                        }
                        Spacer()
                        if candidate["detected"] as? Bool == true {
                            Button("Utiliser") {
                                app.desktopSocketPath = candidate["path"] as? String ?? ""
                                app.desktopConnectionVerified = false
                                app.desktopConnectionNote = "Chemin sélectionné. Testez la connexion avant de l’enregistrer."
                            }.disabled(app.desktopConnectionBusy)
                        } else {
                            Text("Indisponible").font(.caption).foregroundStyle(.secondary)
                        }
                    }
                }
                if (app.desktopConnection["candidates"] as? [[String: Any]] ?? []).isEmpty {
                    Text("Aucun socket détecté sur ce Mac.").font(.callout).foregroundStyle(.secondary)
                }
                Divider()
                TextField("Chemin absolu du socket", text: $app.desktopSocketPath).textFieldStyle(.roundedBorder).disabled(app.desktopConnectionBusy)
                HStack(spacing: 10) {
                    Button("Tester") { app.testDesktopConnection() }.disabled(app.desktopConnectionBusy || app.desktopSocketPath.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                    Button("Enregistrer") { app.testDesktopConnection(save: true) }
                        .disabled(app.desktopConnectionBusy || !app.desktopConnectionVerified || app.desktopTestedSocket != app.desktopSocketPath.trimmingCharacters(in: .whitespacesAndNewlines))
                    Spacer()
                    if app.desktopConnectionBusy { InlineStatus(busy: true, message: app.desktopConnectionNote) }
                }
                if !app.desktopConnectionNote.isEmpty { InlineStatus(busy: false, message: app.desktopConnectionNote) }
                SettingsHint(
                    text: "Un test réussi confirme le service, pas la soumission dans Desktop.",
                    detail: "Le test initialise uniquement le protocole Quick Apply : aucun envoi de prompt, aucun appel au modèle.")
                Button("Rétablir la détection automatique") { app.restoreDesktopConnectionDetection() }
                    .disabled(app.desktopConnectionBusy || !customSocket)
            }
            SettingsCard(title: "Routes de soumission",
                         status: "Lecture seule",
                         ok: true) {
                let routes = app.desktopConnection["routes"] as? [String: Any] ?? [:]
                RouteLine(title: "Dispatch natif QTi — canal principal",
                          route: routes["native_patch"] as? [String: Any] ?? [:],
                          fallbackDetail: "Soumission via le socket du daemon claude-desktopd.")
                RouteLine(title: "CDP externe — canal optionnel historique",
                          route: routes["cdp_web"] as? [String: Any] ?? [:],
                          fallbackDetail: "Aucune cible CDP activée ; activation par configuration uniquement.")
                SettingsHint(
                    text: "L’activation de la route CDP externe se fait par configuration ; cette fenêtre reflète seulement l’état réel.",
                    detail: "Le dispatch natif QTi passe par le socket du daemon claude-desktopd. La route CDP externe n’est proposée que si une cible CDP est activée en configuration ; aucun bouton d’activation ici, l’état affiché est celui lu du daemon. La route ChatGPT CDP pipe est un mécanisme distinct.")
            }
            SettingsCard(title: "Soumission Quick Apply",
                         status: "Aucune autorisation requise",
                         ok: true) {
                SettingsHint(
                    text: "Aucune autorisation macOS n’est requise pour Reprise.",
                    detail: "Un seul socket Quick Apply : ~/.claude-desktop/quick-entry.sock, tenu par l’app ReClaude patchée (fusion 10/11/2026). Un socket présent + greeting vérifié = ReClaude lancée ; socket orphelin ou absent = ReClaude arrêtée.")
            }
        }
    }
    var healthActions: [(text: String, page: String?, tab: String?)] {
        let features = app.health["quick_entry_features"] as? [String: Any] ?? [:]
        let claude = (features["anomalies"] as? [String: Any])?["anomalies"] as? [[String: Any]] ?? []
        let codex = app.health["codex_anomalies"] as? [[String: Any]] ?? []
        var items: [(text: String, page: String?, tab: String?)] = []
        for action in app.health["actions_for_human"] as? [String] ?? [] {
            let nav = healthNavigation(action)
            items.append((action, nav.page, nav.tab))
        }
        for (source, anomalies, page, tab) in [("Claude Desktop", claude, "connections", "quickapply"),
                                               ("Codex", codex, "connections", "codex")] {
            for anomaly in anomalies {
                let component = anomaly["component"] as? String ?? "Composant"
                let detail = anomaly["detail"] as? String ?? "Anomalie à examiner"
                items.append((source + " — " + component + " : " + detail, page, tab))
            }
        }
        return items
    }

    func healthNavigation(_ text: String) -> (page: String?, tab: String?) {
        let lowered = text.lowercased()
        if lowered.contains("harness") { return ("integrations", nil) }
        if lowered.contains("claude") { return ("connections", "quickapply") }
        if lowered.contains("codex") { return ("connections", "codex") }
        return (nil, nil)
    }

    var healthReviewItems: [String] {
        healthActions.map { $0.text }
    }
    var health: some View {
        let versions = app.health["versions"] as? [String: Any] ?? [:]
        let actions = healthReviewItems
        let stale = app.health["stale_log_signatures"] as? [String] ?? []
        let relaunch = app.health["needs_relaunch"] as? [String] ?? []
        let features = app.health["quick_entry_features"] as? [String: Any] ?? [:]
        let featureChecks = features["checks"] as? [String: Any] ?? [:]
        let codexFeatures = app.health["codex_features"] as? [String: Any] ?? [:]
        let codexChecks = codexFeatures["checks"] as? [String: Any] ?? [:]
        let codexAnomalies = app.health["codex_anomalies"] as? [[String: Any]] ?? []
        let actionCount = actions.count
        let verified = app.healthVerified
        return VStack(alignment: .leading, spacing: 18) {
            if let check = app.integrationVerification,
               check["status"] as? String == "verified",
               check["update_required"] as? Bool == true {
                SettingsBanner(ok: false, title: "Mise à jour des connecteurs nécessaire",
                               detail: "Le contrôle Harness signale une délégation absente ou une release dépassée. Corriger dans Intégrations et recharger le client.")
            }
            SettingsBanner(
                ok: verified && actionCount == 0,
                title: verified ? (actionCount == 0 ? "Tout est sain" : actionCount == 1 ? "1 élément à examiner" : "\(actionCount) éléments à examiner")
                                : (app.healthCheckedAt == nil ? "Contrôle requis" : "Contrôle périmé"),
                detail: verified && actionCount == 0
                    ? "Aucune action nécessaire après la dernière mise à jour."
                    : app.healthCheckedAt.map { "Dernier contrôle réussi : " + localDateTime($0) + "." } ?? "Aucun contrôle vérifié pour l’instant.")
            SettingsCard(title: "Relancer Reprise",
                         status: "Disponible",
                         ok: true) {
                HStack(spacing: 10) {
                    Button("Relancer Reprise") { app.relaunchApp() }.disabled(app.relaunchBusy)
                    if app.chainSupported {
                        Button("Relance par script (dev)") { app.restartChain() }.disabled(app.chainBusy)
                    }
                    Spacer()
                    if app.relaunchBusy || app.chainBusy {
                        InlineStatus(busy: true, message: app.relaunchNote.isEmpty ? app.chainNote : app.relaunchNote)
                    }
                }
                if !app.relaunchNote.isEmpty { InlineStatus(busy: false, message: app.relaunchNote) }
                if app.chainSupported && !app.chainNote.isEmpty { InlineStatus(busy: false, message: app.chainNote) }
                SettingsHint(
                    text: "L’interface redémarre ; les applications liées, codexd et le scheduler ne sont pas arrêtés.",
                    detail: "La relance recrée le processus de l’app depuis son bundle — disponible dans toutes les distributions. La relance par script reste réservée aux lancements de développement (archive runtime).")
            }
            SettingsCard(title: "Éléments à examiner",
                         status: verified ? (actionCount == 0 ? "Rien à faire" : actionCount == 1 ? "1 point" : "\(actionCount) points") : "Non vérifié",
                         ok: verified && actionCount == 0) {
                let actions = healthActions
                if actions.isEmpty {
                    Text(verified ? "Rien à examiner pour l’instant." : "Aucune conclusion possible sans contrôle vérifié.")
                        .font(.callout).foregroundStyle(.secondary)
                } else {
                    ForEach(Array(actions.enumerated()), id: \.offset) { _, action in
                        HStack(spacing: 8) {
                            Label(action.text, systemImage: "hand.point.right").font(.callout)
                            Spacer()
                            if let page = action.page {
                                Button("Ouvrir") { sectionTarget(page, action.tab) }
                                    .controlSize(.small)
                            }
                        }
                    }
                }
                if !relaunch.isEmpty || !stale.isEmpty {
                    SettingsHint(
                        text: "Détails techniques du contrôle.",
                        detail: [
                            relaunch.isEmpty ? nil : "Processus à relancer : " + relaunch.joined(separator: ", "),
                            stale.isEmpty ? nil : "Signatures de journaux obsolètes : " + stale.joined(separator: ", ")
                        ].compactMap { $0 }.joined(separator: "\n"),
                        detailLabel: "Détail")
                }
            }
            SettingsCard(title: "Versions détectées",
                         status: versions.isEmpty ? "Aucune" : (versions.count == 1 ? "1 application" : "\(versions.count) applications"),
                         ok: !versions.isEmpty) {
                if versions.isEmpty { Text("Aucune version lue.").font(.callout).foregroundStyle(.secondary) }
                ForEach(Array(versions.keys.sorted()), id: \.self) { key in
                    let item = versions[key] as? [String: Any] ?? [:]
                    let fields = key == "codex" ? ["desktop_version", "desktop_build", "cli_version"] : ["desktop_version", "desktop_build"]
                    VStack(alignment: .leading, spacing: 4) {
                        Text(key).font(.callout.weight(.semibold))
                        ForEach(fields, id: \.self) { field in
                            let status = item[field == "cli_version" ? "cli_status" : (key == "codex" ? "desktop_status" : "status")] as? String
                            let fallback = status == "absent" ? "Non installé" : (status == "unreadable" ? "Lecture impossible" : "Non renseigné")
                            let legacy = field == "desktop_version" ? versions[key] as? String : nil
                            let label = field == "cli_version" ? "CLI" : (field == "desktop_build" ? "Build Desktop" : "Desktop")
                            Text(label + " : " + (item[field] as? String ?? legacy ?? fallback))
                        }
                        if let path = item["desktop_plist"] as? String ?? item["plist_path"] as? String {
                            Text(path).font(.caption).textSelection(.enabled)
                        }
                        if let path = item["cli_path"] as? String { Text(path).font(.caption).textSelection(.enabled) }
                    }.font(.callout.monospaced()).foregroundStyle(.secondary)
                }
                SettingsHint(
                    text: "Les versions ne disent pas si une reprise fonctionne.",
                    detail: "Reprise lit les versions et les composants de Claude Desktop et de Codex (Desktop et CLI), signale les relances nécessaires et vérifie les signatures des journaux Claude après une mise à jour et une activité pertinente.")
            }
            SettingsCard(title: "Claude Desktop · soumission Quick Apply",
                         status: featureChecks.isEmpty ? "Aucun relevé" : "Vérifié",
                         ok: !featureChecks.isEmpty) {
                ForEach(Array(featureChecks.keys.sorted()), id: \.self) { key in
                    let item = featureChecks[key] as? [String: Any] ?? [:]
                    HStack(spacing: 8) {
                        PresenceCheckLabel(present: item["present"] as? Bool == true, item: item["label"] as? String ?? key)
                        Text(item["label"] as? String ?? key).font(.callout)
                        Spacer()
                    }
                }
                if let shortcuts = featureChecks["preferences"] as? [String: Any], let sc = shortcuts["shortcuts"] as? [String: Any] {
                    Text("Raccourcis : " + ((sc["quickEntryShortcut"] as? String) ?? "—") + " · dictée : " + ((sc["quickEntryDictationShortcut"] as? String) ?? "—"))
                        .font(.caption).foregroundStyle(.secondary)
                }
                let anomalies = (features["anomalies"] as? [String: Any])?["anomalies"] as? [[String: Any]] ?? []
                if !anomalies.isEmpty {
                    Divider()
                    ForEach(Array(anomalies.enumerated()), id: \.offset) { _, a in
                        HStack(spacing: 8) {
                            Label((a["detail"] as? String ?? "") + " — " + (a["component"] as? String ?? ""), systemImage: "exclamationmark.triangle")
                                .font(.callout).foregroundStyle(.orange)
                            Spacer()
                            Button("Ouvrir") { app.connectionTab = "quickapply" }
                                .controlSize(.small)
                        }
                    }
                }
                SettingsHint(
                    text: "La synthèse Apple Intelligence se trouve dans Analyse locale.",
                    detail: "La présence des composants ne prouve pas qu’une soumission aboutira. Vérifiez la connexion au daemon claude-desktopd, la disponibilité du relais ReClaude et, séparément, les preuves de livraison et de traitement (« arrivé ≠ traité »).")
            }
            SettingsCard(title: "Codex · interaction avec Reprise",
                         status: codexChecks.isEmpty ? "Aucun relevé" : "Vérifié",
                         ok: !codexChecks.isEmpty) {
                ForEach(Array(codexChecks.keys.sorted()), id: \.self) { key in
                    let item = codexChecks[key] as? [String: Any] ?? [:]
                    HStack(spacing: 8) {
                        PresenceCheckLabel(present: item["present"] as? Bool == true, item: item["label"] as? String ?? key)
                        Text(item["label"] as? String ?? key).font(.callout)
                        Spacer()
                    }
                }
                if !codexAnomalies.isEmpty {
                    Divider()
                    ForEach(Array(codexAnomalies.enumerated()), id: \.offset) { _, a in
                        HStack(spacing: 8) {
                            Label((a["detail"] as? String ?? "") + " — " + (a["component"] as? String ?? ""), systemImage: "exclamationmark.triangle")
                                .font(.callout).foregroundStyle(.orange)
                            Spacer()
                            Button("Ouvrir") { app.connectionTab = "codex" }
                                .controlSize(.small)
                        }
                    }
                }
            }
        }
    }
    var privacy: some View {
        let keyStatus = app.vaultAvailability["status"] as? String ?? "not_verified"
        let keyLabel = app.vaultAccessVerified ? "Clé accessible"
            : keyStatus == "unavailable" ? "Clé indisponible"
            : keyStatus == "error" ? "Contrôle impossible"
            : keyStatus == "verified" ? "Contrôle périmé" : "Non vérifié"
        return VStack(alignment: .leading, spacing: 18) {
            SettingsCard(title: "Chiffrement local · mécanisme et preuve",
                         status: keyLabel, ok: app.vaultAccessVerified) {
                VStack(alignment: .leading, spacing: 10) {
                    Label("AES-256-GCM (CryptoKit) : mécanisme prévu pour les caches locaux", systemImage: "lock.shield")
                    Label("Clé conservée dans le Trousseau macOS : disponibilité vérifiée séparément", systemImage: "key")
                    Label("Aucune publication dans Spotlight par Reprise", systemImage: "eye.slash")
                    Label("Traitement Apple sur l’appareil lorsqu’une route locale est sélectionnée", systemImage: "desktopcomputer")
                    Label("Purge du cache prévue après sept jours", systemImage: "calendar")
                    HStack {
                        Button("Vérifier la clé sans autorisation") { app.checkVaultAvailability() }
                            .disabled(app.vaultChecking || app.vaultBusy)
                        if app.vaultChecking { ProgressView().controlSize(.small) }
                    }
                    if let checkedAt = app.vaultAvailabilityCheckedAt {
                        Text("Source : contrôle non interactif de la clé existante · " + localDateTime(checkedAt))
                            .font(.caption).foregroundStyle(.secondary)
                    } else {
                        Text("Aucun contrôle récent de disponibilité de la clé.")
                            .font(.caption).foregroundStyle(.secondary)
                    }
                    SettingsHint(
                        text: "Clé accessible ne signifie pas index vérifié.",
                        detail: "Ce contrôle ne crée pas de clé, ne déchiffre pas l’index et ne vérifie ni le contenu des caches, ni l’effacement, ni les fichiers des applications tierces.")
                }.font(.callout)
            }
            SettingsCard(title: "Cache de recherche",
                         status: app.searching ? "Opération en cours" : "État non vérifié", ok: false) {
                HStack(spacing: 10) {
                    Button("Vider l’index") { app.clearIndex() }.disabled(app.searching)
                    if app.searching { ProgressView().controlSize(.small) }
                }
                if app.searchNote.contains("Index vidé") { InlineStatus(busy: false, message: app.searchNote) }
                SettingsHint(
                    text: "L’existence et le contenu de l’index ne sont pas vérifiés ici.",
                    detail: "La recherche suivante peut reconstruire les éléments récents. Les sessions, journaux et fichiers des autres applications ne sont pas modifiés par la purge de ce cache.")
            }
            SettingsCard(title: "Accès au Trousseau",
                         status: app.vaultBusy ? "Autorisation…" : "À la demande", ok: true) {
                Button(app.vaultBusy ? "Autorisation en cours…" : "Autoriser l’accès au cache") { app.authorizeVault() }
                    .disabled(app.vaultBusy)
                if !app.vaultNote.isEmpty { InlineStatus(busy: false, message: app.vaultNote) }
                SettingsHint(
                    text: "Un refus suspend le cache sans afficher de dialogue.",
                    detail: "En arrière-plan, un refus d’accès au Trousseau met simplement le cache en pause. Ce bouton demande volontairement l’autorisation macOS ; il ne modifie les permissions d’aucune autre application.")
            }
            SettingsHint(
                text: "Le chiffrement ne couvre ni les journaux du planificateur ni ses archives.",
                detail: "Les caches de recherche et d’analyse utilisent le mécanisme AES-GCM. La disponibilité d’une clé ne prouve pas qu’un cache particulier soit actuellement chiffré. Les instructions du planificateur, ses journaux et ses archives ZIP ne sont pas couverts ; les sources des applications tierces restent inchangées.")
        }
        .onAppear { app.checkVaultAvailability() }
    }
    func empty(_ title: String, icon: String, detail: String) -> some View {
        VStack(spacing: 12) { Image(systemName: icon).font(.system(size: 36, weight: .light)).foregroundStyle(.secondary); Text(title).font(.title3.weight(.semibold)); Text(detail).foregroundStyle(.secondary).multilineTextAlignment(.center).frame(maxWidth: 380) }.frame(maxWidth: .infinity).padding(.vertical, 44)
    }
    func sessionTitle(_ row: [String: Any]) -> String {
        let title = row["title"] as? String ?? "Session"
        return title.hasPrefix("Session ") ? title + " · " + String((row["id"] as? String ?? "").suffix(6)) : title
    }
    // Local time, 24-hour clock, for every timestamp shown in the UI.
    static let localTime: DateFormatter = {
        let f = DateFormatter()
        f.locale = Locale(identifier: "fr_FR")   // 24-hour clock
        f.timeZone = .current                     // user's local time zone
        f.dateFormat = "HH:mm"
        return f
    }()
    static let localDateTime: DateFormatter = {
        let f = DateFormatter()
        f.locale = Locale(identifier: "fr_FR")
        f.timeZone = .current
        f.dateFormat = "dd/MM HH:mm"
        return f
    }()
    func localTime(_ date: Date) -> String { Self.localTime.string(from: date) }
    func localDateTime(_ date: Date) -> String { Self.localDateTime.string(from: date) }
    func quotaStatusLabel(_ status: String) -> String {
        switch status {
        case "waiting": return "En attente de quota"
        case "awaiting_observation": return "Observation en attente"
        case "uncertain": return "Envoi incertain · doublon possible"
        case "dispatched": return "Envoyé"
        case "admitted": return "Admis"
        case "paused": return "En pause"
        default: return status
        }
    }

    // Attention count per sidebar section, so the user sees where to act
    // without visiting every sidebar section.
    func sidebarBadge(_ section: String) -> Int? {
        switch section {
        case "sessions": return app.attention.count
        case "resumptions": return app.verdicts.count
        case "health": return app.healthVerified ? healthReviewItems.count : nil
        default: return nil
        }
    }

    func friendlyDate(_ raw: String) -> String {
        let format = ISO8601DateFormatter(); format.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        return format.date(from: raw).map { localDateTime($0) } ?? raw
    }
}
