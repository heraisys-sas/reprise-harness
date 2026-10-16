import Cocoa
import SwiftUI

struct Surface<Content: View>: View {
    @ViewBuilder var content: Content
    var body: some View { content.padding(22).frame(maxWidth: .infinity, alignment: .leading).background(.background, in: RoundedRectangle(cornerRadius: 18)).overlay(RoundedRectangle(cornerRadius: 18).strokeBorder(.primary.opacity(0.06))) }
}
struct Dashboard: View {
    @ObservedObject var app: App
    let sections = [("overview", "Vue d’ensemble", "square.grid.2x2"), ("sessions", "Sessions", "tray"), ("resumptions", "Reprises", "clock.arrow.circlepath"), ("analysis", "Analyse locale", "sparkles"), ("search", "Recherche", "magnifyingglass"), ("connection", "Connexion Codex", "point.3.connected.trianglepath.dotted"), ("privacy", "Confidentialité", "lock.shield")]
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
                    if !app.navigationNote.isEmpty { Label(app.navigationNote, systemImage: "info.circle").font(.callout).foregroundStyle(.secondary) }
                    switch app.page {
                    case "sessions": sessions
                    case "resumptions": resumptions
                    case "analysis": analysis
                    case "search": search
                    case "connection": connection
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
        case "resumptions": return "Les tentatives sont regroupées par cause. Les détails restent accessibles à la demande."
        case "analysis": return "Un second regard sur les comptes rendus, sur votre Mac."
        case "search": return "Retrouvez un élément sans vous souvenir de l’application."
        case "connection": return "Connectez Reprise à un serveur existant, sans envoyer de message à un agent."
        case "privacy": return "Vos données de recherche restent sous votre contrôle."
        default: return "L’essentiel de vos sessions, sans les interrompre."
        }
    }
    var overview: some View {
        VStack(alignment: .leading, spacing: 20) {
            if app.lastRefresh == nil && app.busy {
                Surface { HStack(spacing: 12) { ProgressView().controlSize(.small); Text("Lecture de l’activité locale…").foregroundStyle(.secondary) } }
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
                Surface {
                    HStack(spacing: 16) {
                        Image(systemName: "clock.arrow.circlepath").font(.title2).foregroundStyle(.secondary)
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
                    Text("\(app.attention.count) signalements récents dans les sessions").foregroundStyle(.secondary)
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
                    Label("Service de reprise indisponible", systemImage: "exclamationmark.triangle").foregroundStyle(.orange)
                } else if (app.scheduler["running"] as? Int ?? 0) + (app.scheduler["launching"] as? Int ?? 0) == 0 && Date().timeIntervalSince1970 - (app.scheduler["last_tick"] as? Double ?? 0) > 180 {
                    Label("Dernière vérification du service trop ancienne", systemImage: "exclamationmark.triangle").foregroundStyle(.orange)
                } else if let error = app.scheduler["last_error"] as? String, !error.isEmpty {
                    Label(error, systemImage: "exclamationmark.triangle").foregroundStyle(.orange)
                } else {
                    let running = app.scheduler["running"] as? Int ?? 0
                    let launching = app.scheduler["launching"] as? Int ?? 0
                    let pending = app.scheduler["pending"] as? Int ?? 0
                    Text(running > 0 ? "\(running) tour(s) démarré(s)" : launching > 0 ? "Connexion au moteur · démarrage non confirmé" : pending > 0 ? "\(pending) reprise(s) en attente" : "Aucune reprise en attente").font(.title3.weight(.semibold))
                    Text(app.scheduler["auto_enabled"] as? Bool == true ? "Surveillance automatique activée" : "Automatisation en pause").foregroundStyle(.secondary)
                    if let due = app.scheduler["next_due"] as? Double {
                        Text("Prochaine échéance : " + Date(timeIntervalSince1970: due).formatted(date: .abbreviated, time: .shortened)).font(.caption).foregroundStyle(.secondary)
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
                            if let stamp = group["last_at"] as? Double { Text("Dernière tentative : " + Date(timeIntervalSince1970: stamp).formatted(date: .abbreviated, time: .shortened)).font(.caption).foregroundStyle(.secondary) }
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
            if let stamp = task["ended_at"] as? Double ?? task["due"] as? Double { Text(Date(timeIntervalSince1970: stamp).formatted(date: .abbreviated, time: .shortened)).font(.caption).foregroundStyle(.secondary) }
            let source = app.rows.first { ($0["id"] as? String) == (task["session"] as? String) && ($0["provider"] as? String) == (task["provider"] as? String) }
            let parent = source?["parent_session"] as? String
            Button(parent == nil ? "Ouvrir la session" : "Ouvrir la session parente") {
                app.openSession(["provider": task["provider"] as? String ?? "Codex", "id": parent ?? task["session"] as? String ?? ""])
            }.buttonStyle(.link)
        }.frame(maxWidth: .infinity, alignment: .leading)
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
                return (app.sessionFilter == "Récentes" && app.attention.contains { ($0["id"] as? String) == (row["id"] as? String) }) || app.sessionFilter == "Toutes" || (app.sessionFilter == "Erreurs" && ["failed", "quota_candidate", "quota_confirmed", "rate_limited"].contains(state)) || (app.sessionFilter == "Arrêts" && state == "stopped") || (app.sessionFilter == "À examiner" && ["unfinished", "needs_user"].contains(state))
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
                        Button { app.openSession(row) } label: {
                        HStack(spacing: 12) {
                            Image(systemName: row["state"] as? String == "stopped" ? "pause.circle" : "exclamationmark.circle").font(.title2).foregroundStyle(.secondary)
                            VStack(alignment: .leading, spacing: 4) { Text(sessionTitle(row)).font(.headline); Text((row["provider"] as? String ?? "") + " · " + ((row["updated"] as? Double).map { Date(timeIntervalSince1970: $0).formatted(date: .abbreviated, time: .shortened) } ?? "Date inconnue")).font(.caption).foregroundStyle(.secondary) }
                            Spacer(); Text(row["state"] as? String == "stopped" ? "Arrêtée" : "À vérifier").font(.caption).foregroundStyle(.secondary)
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
            Surface { HStack { VStack(alignment: .leading, spacing: 6) { Label("Apple Intelligence", systemImage: "sparkles").font(.headline); Text(app.analysis["status"] as? String ?? "En attente").font(.callout).foregroundStyle(.secondary) }; Spacer(); Button(app.analysisBusy ? "Analyse…" : "Analyser") { app.analyzeLocal() }.disabled(app.analysisBusy) } }
            if app.verdicts.isEmpty { empty("Aucun signalement à afficher", icon: "text.badge.checkmark", detail: "L’absence de signalement ne garantit pas la réussite d’une session. Les vérifications du projet restent la référence.") }
            ForEach(Array(app.verdicts.enumerated()), id: \.offset) { _, value in Surface { VStack(alignment: .leading, spacing: 10) { Button { app.openSession(value) } label: { Text(value["title"] as? String ?? "Session").font(.headline) }.buttonStyle(.plain).help("Afficher l’application de cette session"); Text(value["reason"] as? String ?? ""); Text(value["evidence"] as? String ?? "").font(.callout).foregroundStyle(.secondary).textSelection(.enabled) } } }
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
            ForEach(Array(app.searchResults.enumerated()), id: \.offset) { _, value in Surface { VStack(alignment: .leading, spacing: 8) { Button { app.openSession(value) } label: { Text(value["title"] as? String ?? "Session").font(.headline) }.buttonStyle(.plain).help("Afficher l’application de cette session"); Text((value["provider"] as? String ?? "") + " · " + String((value["id"] as? String ?? "").suffix(8))).font(.caption).foregroundStyle(.secondary); Text(value["text"] as? String ?? "").textSelection(.enabled) } } }
        }
    }
    var connection: some View {
        VStack(alignment: .leading, spacing: 18) {
            Surface {
                VStack(alignment: .leading, spacing: 14) {
                    Label("Transport des reprises Codex", systemImage: "point.3.connected.trianglepath.dotted").font(.headline)
                    Text(app.connection.isEmpty ? "Configuration indisponible" : app.connection["mode"] as? String == "server" ? "Socket personnalisé enregistré" : "Détection automatique · socket local ou CLI").font(.callout)
                    if let path = app.connection["configured_socket"] as? String { Text(path).font(.caption.monospaced()).foregroundStyle(.secondary).textSelection(.enabled) }
                    Text("Une connexion réussie confirme le serveur, pas sa capacité à reprendre les sessions ouvertes dans Desktop.").font(.callout).foregroundStyle(.secondary)
                }
            }
            Surface {
                VStack(alignment: .leading, spacing: 14) {
                    Text("Serveurs locaux").font(.headline)
                    ForEach(Array((app.connection["candidates"] as? [[String: Any]] ?? []).enumerated()), id: \.offset) { _, candidate in
                        HStack(alignment: .top, spacing: 12) {
                            Image(systemName: candidate["detected"] as? Bool == true ? "circle.fill" : "circle").font(.caption).foregroundStyle(.secondary).padding(.top, 4)
                            VStack(alignment: .leading, spacing: 5) {
                                Text(candidate["label"] as? String ?? "Serveur").font(.callout.weight(.semibold))
                                Text(candidate["detected"] as? Bool == true ? "Socket détecté · connexion à tester" : "Socket indisponible").font(.caption).foregroundStyle(.secondary)
                                Text(candidate["path"] as? String ?? "").font(.caption.monospaced()).foregroundStyle(.secondary).textSelection(.enabled)
                            }
                            Spacer()
                            Button("Choisir") {
                                app.socketPath = candidate["path"] as? String ?? ""
                                app.connectionVerified = false
                                app.connectionNote = "Chemin sélectionné. Testez la connexion avant de l’enregistrer."
                            }.disabled(app.connectionBusy)
                        }
                    }
                }
            }
            Surface {
                VStack(alignment: .leading, spacing: 14) {
                    TextField("Chemin absolu du socket", text: $app.socketPath).textFieldStyle(.roundedBorder).disabled(app.connectionBusy)
                    HStack {
                        Button("Tester la connexion") { app.testConnection() }.disabled(app.connectionBusy || app.socketPath.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                        Button("Utiliser ce serveur") { app.testConnection(save: true) }
                            .disabled(app.connectionBusy || !app.connectionVerified || app.testedSocket != app.socketPath.trimmingCharacters(in: .whitespacesAndNewlines))
                        Spacer()
                        if app.connectionBusy { ProgressView().controlSize(.small) }
                    }
                    if !app.connectionNote.isEmpty { Text(app.connectionNote).font(.callout).foregroundStyle(.secondary).textSelection(.enabled) }
                    Text("Le test initialise uniquement le protocole. Aucun appel au modèle, aucune reprise de session.").font(.caption).foregroundStyle(.secondary)
                    Divider()
                    Button("Rétablir la détection automatique") { app.restoreConnectionDetection() }.disabled(app.connectionBusy || app.connection["mode"] as? String != "server")
                }
            }
            Surface {
                VStack(alignment: .leading, spacing: 12) {
                    Text("Serveur dédié et Codex Desktop").font(.headline)
                    Text("Reprise peut fournir --listen au démarrage d’un serveur dédié. Ce serveur gère ses propres sessions ; il ne récupère pas celles détenues par Desktop.").font(.callout).foregroundStyle(.secondary)
                    Text("Le raccordement de Desktop à un serveur externe reste à valider sur cette installation avant une activation au démarrage.").font(.callout).foregroundStyle(.secondary)
                }
            }
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
