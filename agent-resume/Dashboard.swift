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
    let sections = [("overview", "Vue d’ensemble", "square.grid.2x2"), ("sessions", "Sessions", "tray"), ("resumptions", "Reprises", "clock.arrow.circlepath"), ("analysis", "Analyse locale", "sparkles"), ("search", "Recherche", "magnifyingglass"), ("connections", "Connexions", "point.3.connected.trianglepath.dotted"), ("integrations", "Intégrations", "puzzlepiece.extension"), ("health", "Santé post-update", "heart.text.square"), ("privacy", "Confidentialité", "lock.shield")]
    var title: String { sections.first { $0.0 == app.page }?.1 ?? "Reprise" }
    @ViewBuilder var body: some View {
        if app.stateReady { normalDashboard } else { StateSetup(app: app) }
    }
    var normalDashboard: some View {
        NavigationSplitView {
            VStack(alignment: .leading, spacing: 0) {
                HStack(spacing: 10) { Image(systemName: "arrow.clockwise.circle.fill").font(.title2).foregroundStyle(.primary); Text("Reprise").font(.title2.weight(.semibold)) }.padding(20)
                List(selection: Binding<String?>(get: { app.page }, set: { app.page = $0 ?? "overview" })) {
                    Section("Activité") {
                        ForEach(sections, id: \.0) { entry in
                            HStack {
                                Label(entry.1, systemImage: entry.2)
                                Spacer()
                                if let count = sidebarBadge(entry.0), count > 0 {
                                    Text("\(count)")
                                        .font(.caption.monospacedDigit())
                                        .foregroundStyle(.secondary)
                                        .accessibilityLabel("\(entry.1) : \(count) élément(s) à examiner")
                                }
                            }
                            .contentShape(Rectangle())
                            .tag(entry.0)
                        }
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
                    case "sessions": sessions
                    case "resumptions": resumptions
                    case "analysis": analysis
                    case "search": search
                    case "connections": connections
                    case "integrations": Integrations(app: app)
                    case "health": health
                    case "privacy": privacy
                    default: overview
                    }
                }.padding(28).frame(maxWidth: 1100, alignment: .leading)
            }.background(Color(nsColor: .windowBackgroundColor)).navigationTitle(title)
            .toolbar {
                ToolbarItemGroup {
                    if app.busy { ProgressView().controlSize(.small) }
                    Button { app.page = "search" } label: { Label("Rechercher", systemImage: "magnifyingglass") }.help("Recherche privée").keyboardShortcut("f", modifiers: .command)
                    Button { app.refresh() } label: { Label("Actualiser", systemImage: "arrow.clockwise") }.disabled(app.busy).keyboardShortcut("r", modifiers: .command).accessibilityLabel("Actualiser les données")
                }
            }
            .safeAreaInset(edge: .bottom) {
                HStack { Label("Lecture locale", systemImage: "internaldrive"); Spacer(); if let date = app.lastRefresh { Text("Actualisé à " + localTime(date)) } }.font(.caption).foregroundStyle(.secondary).padding(.horizontal, 24).padding(.vertical, 10).background(.bar)
            }
        }
    }
    var subtitle: String {
        switch app.page {
        case "sessions": return "Distinguez les interruptions récentes des arrêts et de l’historique."
        case "resumptions": return "Les tentatives sont regroupées par cause. Les détails restent accessibles à la demande."
        case "analysis": return "Un second regard sur les comptes rendus, sur votre Mac."
        case "search": return "Retrouvez un élément sans vous souvenir de l’application."
        case "connections": return "Connectez Reprise aux services codexd (codex-desktop) et claude-desktopd (Quick Apply)."
        case "integrations": return "Préparez explicitement les outils MCP et les consignes de votre profil Harness."
        case "health": return "Après une mise à jour : ce qu’il faut relancer, ce qui reste à parser, et les actions humaines."
        case "privacy": return "Vos données de recherche restent sous votre contrôle."
        default: return "L’essentiel de vos sessions, sans les interrompre."
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
                            }.accessibilityElement(children: .combine).accessibilityLabel(entry.0 + " : " + (entry.3.map { $0 > 0 ? "\($0) en cours" : "au repos" } ?? "état indisponible"))
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
                        Text(freshness == "recent_read" ? "Lecture récente · aucune reprise déduite" : "Quota actuel inconnu · lecture absente ou périmée").font(.caption).foregroundStyle(.secondary)
                        if let stamp = knowledge["observed_at"] as? Double { Text("Lu le " + localDateTime(Date(timeIntervalSince1970: stamp))).font(.caption).foregroundStyle(.secondary) }
                        if let pools = knowledge["pools"] as? [[String: Any]], !pools.isEmpty {
                            ForEach(Array(pools.enumerated()), id: \.offset) { _, pool in
                                VStack(alignment: .leading, spacing: 8) {
                                    Text("Pool : " + (pool["limit_id"] as? String ?? "inconnu")).font(.callout.weight(.semibold))
                                    if let windows = pool["windows"] as? [[String: Any]] {
                                        HStack(alignment: .top, spacing: 28) {
                                            ForEach(Array(windows.enumerated()), id: \.offset) { _, window in
                                                quotaRow(window["name"] as? String == "secondary" ? "Fenêtre secondaire" : "Fenêtre primaire", value: window, attributed: pool["quota_pool_key"] as? String != nil)
                                            }
                                        }
                                    }
                                }
                            }
                        } else { Text("Aucune fenêtre fournisseur connue pour cette route.").foregroundStyle(.secondary) }
                        if let quota = app.snapshot["codex_quota"] as? [String: Any], let rates = quota["rates"] as? [String: Any] {
                            DisclosureGroup("Dernier signal de session · compte non attribué") {
                                HStack(alignment: .top, spacing: 28) {
                                    quotaRow("Fenêtre primaire", value: rates["primary"] as? [String: Any])
                                    quotaRow("Fenêtre secondaire", value: rates["secondary"] as? [String: Any])
                                }
                                if let stamp = quota["measured_at"] as? String { Text("Événement du " + friendlyDate(stamp)).font(.caption).foregroundStyle(.secondary) }
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
                        Button("Comprendre") { app.page = "resumptions"; app.resumeFilter = "À examiner" }
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
                    Text(running > 0 ? "\(running) tour(s) démarré(s)" : launching > 0 ? "Connexion au moteur · démarrage non confirmé" : pending > 0 ? "\(pending) reprise(s) en attente" : "Aucune reprise en attente").font(.title3.weight(.semibold))
                    Text(app.scheduler["auto_enabled"] as? Bool == true ? "Surveillance automatique activée" : "Automatisation en pause").foregroundStyle(.secondary)
                    if let due = app.scheduler["next_due"] as? Double {
                        Text("Prochaine échéance : " + localDateTime(Date(timeIntervalSince1970: due))).font(.caption).foregroundStyle(.secondary)
                    }
                    if pending + running + launching > 0 {
                        Button("Voir les reprises") { app.page = "resumptions"; app.resumeFilter = "En attente" }.buttonStyle(.link)
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
                if let minutes, minutes > 0 { Text("Durée déclarée : \(minutes) min").font(.caption).foregroundStyle(.secondary) }
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
        return Surface {
            VStack(alignment: .leading, spacing: 12) {
                HStack {
                    Text("Sessions Claude Desktop · observation").font(.headline)
                    Spacer()
                    Button("Configurer") { app.page = "connections"; app.connectionTab = "quickapply" }.buttonStyle(.link)
                }
                Text(!statusKnown ? "État de configuration inconnu · \(rootStatus)" : enabled ? "Observation CDP opt-in · sessions ouvertes seulement" : "Observation désactivée · aucune lecture CDP").font(.caption).foregroundStyle(.secondary)
                if sessions.isEmpty {
                    Text(statusKnown ? "Aucune session Desktop déclarée. L’observation ne crée pas de chat." : "Sessions inconnues tant que l’état du connecteur n’est pas disponible.").font(.callout).foregroundStyle(.secondary)
                }
                ForEach(Array(sessions.enumerated()), id: \.offset) { _, session in
                    claudeSessionSummary(session)
                }
                if let next = route["next_refresh_at"] as? Double, next.isFinite, next > 0 {
                    Text("Prochaine lecture au plus tôt : " + localDateTime(Date(timeIntervalSince1970: next))).font(.caption).foregroundStyle(.secondary)
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
                Text("Présence observée le \(localDateTime(Date(timeIntervalSince1970: lastSeen))) · \(observation["target_count"] as? Int ?? 0) onglet(s) cible(s)").font(.caption).foregroundStyle(.secondary)
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
    // Single "Connexions" tab: an internal picker switches between the two
    // connection panels (codex-desktop / claude-desktop), freeing the sidebar.
    var connections: some View {
        VStack(alignment: .leading, spacing: 18) {
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
                title: app.connection.isEmpty ? "Configuration indisponible" : "Service codexd joignable",
                detail: app.connection.isEmpty ? "Lancez une actualisation pour lire l’état du service."
                    : customSocket ? "Socket personnalisé enregistré." : "Détection automatique du socket local ou de la CLI.")
            SettingsCard(title: "Partage avec Codex Desktop",
                         status: shared["startup_eligible"] as? Bool == true ? "Validé" : "À valider",
                         ok: shared["startup_eligible"] as? Bool == true) {
                HStack(spacing: 10) {
                    Button("1. Préparer codexd") { app.sharedAction("prepare") }.disabled(app.sharedBusy)
                    Button("2. Démarrer l’appli liée") { app.sharedAction("launch") }
                        .disabled(app.sharedBusy || app.running.contains("com.openai.codex"))
                    Spacer()
                    if app.sharedBusy { InlineStatus(busy: true, message: app.sharedNote) }
                }
                if app.running.contains("com.openai.codex") {
                    Label("Desktop est ouvert : fermez-le normalement avant l’étape 2. Reprise reste ouvert.", systemImage: "exclamationmark.triangle")
                        .font(.caption).foregroundStyle(.orange)
                }
                HStack(spacing: 10) {
                    TextField("Identifiant ou lien du chat à vérifier", text: $app.sharedSession).textFieldStyle(.roundedBorder)
                    Button("3. Vérifier le chat") { app.sharedAction("check") }.disabled(app.sharedBusy || app.sharedSession.isEmpty)
                }
                if !app.sharedNote.isEmpty { InlineStatus(busy: false, message: app.sharedNote) }
                SettingsHint(
                    text: "Après validation, choisissez le socket et réactivez les reprises depuis leur vue.",
                    detail: "La vérification confirme que le chat est bien chargé dans Desktop. Vérifiez aussi les outils de Desktop lui-même avant d’activer le lancement automatique. Les reprises restent en pause jusqu’à leur réactivation manuelle.")
                Toggle("Lancer ce mode à l’ouverture de Reprise", isOn: Binding(get: { app.sharedStartup }, set: { app.setSharedStartup($0) }))
                    .disabled(app.sharedBusy || (shared["startup_eligible"] as? Bool != true && !app.sharedStartup))
                SettingsHint(
                    text: "Disponible seulement après un test réussi avec cette version de Desktop.",
                    detail: "Une mise à jour de Desktop impose une nouvelle validation. Ce réglage concerne le lancement de Reprise, pas la liste des éléments d’ouverture de macOS.")
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
                title: app.desktopConnection.isEmpty ? "Configuration indisponible" : "Service claude-desktopd prêt",
                detail: app.desktopConnection.isEmpty ? "Lancez une actualisation pour lire l’état du service."
                    : customSocket ? "Socket personnalisé enregistré." : "Détection automatique du socket local.")
            SettingsCard(title: "Service et application liée",
                         status: app.desktopStartup ? "Lancement auto" : "Manuel",
                         ok: true) {
                HStack(spacing: 10) {
                    Button("1. Démarrer l’appli liée") { app.desktopAction("launch") }.disabled(app.desktopBusy)
                    Button("2. Démarrer claude-desktopd") { app.desktopAction("start") }.disabled(app.desktopBusy)
                    Button("Arrêter claude-desktopd") { app.desktopAction("stop") }.disabled(app.desktopBusy)
                    if app.desktopBusy { InlineStatus(busy: true, message: app.desktopNote) }
                }
                if !app.desktopNote.isEmpty { InlineStatus(busy: false, message: app.desktopNote) }
                Toggle("Démarrer l’appli liée et claude-desktopd à l’ouverture de Reprise", isOn: Binding(get: { app.desktopStartup }, set: { app.setDesktopStartup($0) }))
                    .disabled(app.desktopBusy)
                SettingsHint(
                    text: "Rien n’est lancé implicitement : ces actions sont explicites.",
                    detail: "Le démarrage automatique n’ouvre que l’application liée et son service au lancement de Reprise. Aucun autre élément d’ouverture macOS n’est modifié.")
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
                    detail: "Le test initialise uniquement le protocole Quick Apply : aucun envoi de prompt, aucun appel au modèle. La soumission réelle dans l’overlay de Desktop dépend des autorisations macOS décrites plus bas.")
                Button("Rétablir la détection automatique") { app.restoreDesktopConnectionDetection() }
                    .disabled(app.desktopConnectionBusy || !customSocket)
            }
            claudeObserverPanel
            SettingsCard(title: "Soumission Quick Apply",
                         status: "Autorisations requises",
                         ok: true) {
                SettingsHint(
                    text: "Accessibilité et Capture d’écran doivent être accordées par vous.",
                    detail: "La soumission réelle dans l’overlay Quick Apply exige les autorisations macOS Accessibilité et Capture d’écran, accordées par l’opérateur dans Réglages Système. Reprise valide et achemine la demande ; elle ne pilote pas l’interface de Claude Desktop à votre place.")
            }
        }
    }
    var claudeObserverPanel: some View {
        let status = app.claudeSessionsStatus
        let route = status["route"] as? [String: Any] ?? [:]
        let binding = status["binding"] as? [String: Any] ?? [:]
        let sessions = status["sessions"] as? [[String: Any]] ?? []
        let capabilities = status["capabilities"] as? [String: Any] ?? [:]
        let lastRead = (route["last_refresh_at"] as? Double).flatMap { $0.isFinite && $0 > 0 ? Date(timeIntervalSince1970: $0) : nil }
        return SettingsCard(title: "Observation des sessions Claude Desktop",
                            status: app.claudeObserverEnabledDraft ? "Active" : "Inactive",
                            ok: app.claudeObserverEnabledDraft) {
            Toggle("Activer l’observation (opt-in)", isOn: $app.claudeObserverEnabledDraft)
                .disabled(app.claudeSessionsBusy)
            HStack(spacing: 12) {
                Text("Port CDP local").font(.caption).foregroundStyle(.secondary)
                TextField("9222", text: $app.claudeObserverPort).frame(width: 90).textFieldStyle(.roundedBorder)
                Text("Intervalle (s)").font(.caption).foregroundStyle(.secondary)
                TextField("60", text: $app.claudeObserverInterval).frame(width: 90).textFieldStyle(.roundedBorder)
                Button("Enregistrer") { app.configureClaudeObserver(enabled: app.claudeObserverEnabledDraft) }
                    .disabled(app.claudeSessionsBusy)
                if app.claudeSessionsBusy { ProgressView().controlSize(.small) }
            }
            SettingsHint(
                text: "Une session associée ne prouve ni l’admission du message ni le retour MCP.",
                detail: "L’association est déclarée par vous. L’observation est limitée aux onglets ouverts et reste en lecture seule. État de la route : "
                    + (route["refresh_status"] as? String ?? status["status"] as? String ?? "inconnu")
                    + " · instance : " + (route["instance_status"] as? String ?? "inconnue")
                    + " · couverture : " + (route["coverage"] as? String ?? "onglets ouverts uniquement")
                    + " · rattachement local : " + (binding["status"] as? String ?? "inconnu")
                    + (lastRead.map { "\nDernière lecture : " + localDateTime($0) } ?? "")
                    + ((route["next_refresh_at"] as? Double).flatMap { $0.isFinite && $0 > 0 ? "\nProchaine observation au plus tôt : " + localDateTime(Date(timeIntervalSince1970: $0)) : nil } ?? ""),
                detailLabel: "État détaillé")
            Divider()
            Text("Associer un chat déjà ouvert").font(.callout.weight(.semibold))
            HStack(spacing: 10) {
                TextField("Identifiant du chat (UUID)", text: $app.claudeSessionChatID).textFieldStyle(.roundedBorder)
                Button("Actualiser") { app.refreshClaudeSessions() }.disabled(app.claudeSessionsBusy)
            }
            Toggle("Je confirme que ce chat est ouvert dans Claude Desktop", isOn: $app.claudeSessionConfirmed)
                .disabled(app.claudeSessionsBusy)
            HStack(spacing: 10) {
                Button("Associer") { app.registerClaudeSession() }
                    .disabled(app.claudeSessionsBusy || !app.claudeSessionConfirmed)
                Button("Retirer l’association") { app.registerClaudeSession(remove: true) }
                    .disabled(app.claudeSessionsBusy)
            }
            if !app.claudeSessionsNote.isEmpty { InlineStatus(busy: false, message: app.claudeSessionsNote) }
            if sessions.isEmpty {
                Text("Aucune session associée.").font(.callout).foregroundStyle(.secondary)
            } else {
                ForEach(Array(sessions.enumerated()), id: \.offset) { _, session in
                    claudeSessionSummary(session)
                }
            }
            if capabilities["action_eligible"] as? Bool != true {
                SettingsHint(
                    text: "Aucune reprise n’est proposée depuis cet écran.",
                    detail: "La pilotabilité, l’admission du message et le retour MCP sont trois capacités distinctes. L’observation ne les prouve pas ; une reprise n’est donc proposée que lorsqu’elles sont toutes vérifiées.")
            }
        }
    }
    var healthReviewItems: [String] {
        let features = app.health["quick_entry_features"] as? [String: Any] ?? [:]
        let claude = (features["anomalies"] as? [String: Any])?["anomalies"] as? [[String: Any]] ?? []
        let codex = app.health["codex_anomalies"] as? [[String: Any]] ?? []
        var items = app.health["actions_for_human"] as? [String] ?? []
        for (source, anomalies) in [("Claude Desktop", claude), ("Codex", codex)] {
            for anomaly in anomalies {
                let component = anomaly["component"] as? String ?? "Composant"
                let detail = anomaly["detail"] as? String ?? "Anomalie à examiner"
                items.append(source + " — " + component + " : " + detail)
            }
        }
        return items
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
        let summary = !verified
            ? (app.healthCheckedAt == nil ? "Santé indisponible · contrôle requis" : "Santé périmée · nouveau contrôle requis")
            : (actionCount == 0 ? "✓ Sain · aucun élément à examiner" : "⚠ \(actionCount) élément(s) à examiner")
        return VStack(alignment: .leading, spacing: 18) {
            SettingsBanner(
                ok: verified && actionCount == 0,
                title: verified ? (actionCount == 0 ? "Tout est sain" : "\(actionCount) élément(s) à examiner")
                                : (app.healthCheckedAt == nil ? "Contrôle requis" : "Contrôle périmé"),
                detail: verified && actionCount == 0
                    ? "Aucune action nécessaire après la dernière mise à jour."
                    : app.healthCheckedAt.map { "Dernier contrôle réussi : " + localDateTime($0) + "." } ?? "Aucun contrôle vérifié pour l’instant.")
            SettingsCard(title: "Redémarrer les services",
                         status: app.chainSupported ? "Disponible" : "Indisponible",
                         ok: app.chainSupported) {
                HStack(spacing: 10) {
                    Button("Tout redémarrer") { app.restartChain() }.disabled(app.chainBusy || !app.chainSupported)
                    Spacer()
                    if app.chainBusy { InlineStatus(busy: true, message: app.chainNote) }
                }
                if !app.chainNote.isEmpty { InlineStatus(busy: false, message: app.chainNote) }
                SettingsHint(
                    text: "Aucune application liée n’est arrêtée de force.",
                    detail: app.chainSupported
                        ? "La relance redémarre Reprise, le service claude-desktopd et le service codexd, puis relit leur état. Les applications liées ne sont jamais terminées de force."
                        : "Le redémarrage groupé est indisponible dans cette distribution (runtime embarqué ou script absent).")
            }
            SettingsCard(title: "Éléments à examiner",
                         status: verified ? (actionCount == 0 ? "Rien à faire" : "\(actionCount) point(s)") : "Non vérifié",
                         ok: verified && actionCount == 0) {
                if actions.isEmpty {
                    Text(verified ? "Rien à examiner pour l’instant." : "Aucune conclusion possible sans contrôle vérifié.")
                        .font(.callout).foregroundStyle(.secondary)
                } else {
                    ForEach(Array(actions.enumerated()), id: \.offset) { _, action in
                        Label(action, systemImage: "hand.point.right").font(.callout)
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
                         status: versions.isEmpty ? "Aucune" : "\(versions.count) application(s)",
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
                        Label((a["detail"] as? String ?? "") + " — " + (a["component"] as? String ?? ""), systemImage: "exclamationmark.triangle")
                            .font(.callout).foregroundStyle(.orange)
                    }
                }
                SettingsHint(
                    text: "La synthèse Apple Intelligence se trouve dans Analyse locale.",
                    detail: "La présence de ces composants est vérifiée de façon déterministe, sans inférence. Elle ne prouve pas qu’une soumission aboutira : Desktop doit être ouvert et les autorisations macOS accordées.")
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
                        Label((a["detail"] as? String ?? "") + " — " + (a["component"] as? String ?? ""), systemImage: "exclamationmark.triangle")
                            .font(.callout).foregroundStyle(.orange)
                    }
                }
            }
        }
    }
    var privacy: some View {
        VStack(alignment: .leading, spacing: 18) {
            SettingsCard(title: "Chiffrement local",
                         status: "Chiffré", ok: true) {
                VStack(alignment: .leading, spacing: 10) {
                    Label("AES-256-GCM (CryptoKit), clé dans le Trousseau macOS", systemImage: "lock.shield")
                    Label("Aucune publication dans Spotlight", systemImage: "eye.slash")
                    Label("Traitement Apple Intelligence sur l’appareil", systemImage: "desktopcomputer")
                    Label("Purge quotidienne après sept jours", systemImage: "calendar")
                }.font(.callout)
            }
            SettingsCard(title: "Cache de recherche",
                         status: app.searching ? "En cours" : "Prêt", ok: true) {
                HStack(spacing: 10) {
                    Button("Vider l’index") { app.clearIndex() }.disabled(app.searching)
                    if app.searching { ProgressView().controlSize(.small) }
                }
                if app.searchNote.contains("Index vidé") { InlineStatus(busy: false, message: app.searchNote) }
                SettingsHint(
                    text: "La recherche suivante reconstruit les éléments récents.",
                    detail: "Le cache ne contient que des données dérivées : les sessions, leurs journaux et les fichiers des autres applications ne sont pas modifiés par cette action.")
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
                detail: "Le chiffrement concerne l’index de recherche et le cache d’analyse. Les instructions du planificateur, ses journaux et ses archives ZIP ne sont pas chiffrés par ce mécanisme. Les sources des autres applications restent inchangées.")
        }
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

    // Attention count per sidebar section, so the user sees where to act
    // without visiting all 9 tabs.
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
