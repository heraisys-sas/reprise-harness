import Cocoa
import SwiftUI

extension App {
    func inspectClaudeIntegration() {
        guard stateReady, !setupBusy else { return }
        setupCommand(["claude-integration-inspect"]) { result in
            self.claudeIntegrationResult = result
            if self.integrationProject.isEmpty, let project = result["project"] as? String { self.integrationProject = project }
        }
    }
    func previewClaudeIntegration(_ action: String) {
        guard stateReady, !setupBusy else { return }
        var args = ["claude-integration-plan", "--project", integrationProject, "--action", action]
        if claudeReplaceProbe { args.append("--replace-probe") }
        setupCommand(args) { result in
            self.claudeIntegrationResult = result
            self.claudeIntegrationPlan = result["plan"] as? [String: Any]
            self.claudeIntegrationAcknowledged = false
        }
    }
    func applyClaudeIntegration() {
        guard stateReady, !setupBusy, claudeIntegrationAcknowledged, let plan = claudeIntegrationPlan else { return }
        setupCommand(["claude-integration-apply"], input: ["plan": plan]) { result in
            self.claudeIntegrationResult = result
            self.claudeIntegrationAcknowledged = false
            if result["status"] as? String == "completed" { self.claudeIntegrationPlan = nil }
        }
    }
    func verifyClaudeIntegration() {
        guard stateReady, !setupBusy else { return }
        setupCommand(["claude-integration-verify"]) { self.claudeIntegrationResult = $0 }
    }

    func saveAAKey() {
        let value = aaKey
        guard stateReady, !setupBusy, !value.trimmingCharacters(in: .whitespaces).isEmpty else { return }
        aaNote = "Écriture de la clé…"
        setupCommand(["aa-key-save"], input: ["key": value]) { result in
            self.aaKey = ""
            if result["saved"] as? Bool == true {
                self.aaNote = "Clé enregistrée hors dépôt. Empreinte SHA-256 : \(result["fingerprint"] as? String ?? "indisponible")."
            } else {
                self.aaNote = result["message"] as? String ?? "Enregistrement non confirmé."
            }
        }
    }

    func refreshClaudeQuota() {
        guard stateReady, !setupBusy else { return }
        setupCommand(["claude-quota-status"]) { self.claudeQuotaState = $0 }
    }
    func configureClaudeQuota(_ enabled: Bool, transport: String? = nil) {
        guard stateReady, !setupBusy else { return }
        var args = ["claude-quota-config", "--enabled", enabled ? "true" : "false"]
        if let transport { args += ["--transport", transport] }
        setupCommand(args) { result in
            if let transport { self.claudeQuotaState["transport"] = transport }
            self.claudeQuotaState["enabled"] = result["claude_desktop_auto_enabled"] as? Bool ?? false
        }
    }

    func invalidateIntegrationPlan() {
        integrationPlan = nil
        integrationAcknowledged = false
        integrationResult = nil
        integrationVerification = nil
    }
    func chooseIntegrationDirectory(project: Bool) {
        guard stateReady, !setupBusy else { return }
        let panel = NSOpenPanel()
        panel.canChooseDirectories = true; panel.canChooseFiles = false; panel.allowsMultipleSelection = false
        panel.showsHiddenFiles = !project
        panel.message = project ? "Choisissez le projet auquel Reprise pourra accéder." : "Choisissez le dossier de configuration Harness."
        if panel.runModal() == .OK, let url = panel.url {
            if project { integrationProject = url.path } else { integrationHome = url.path; integrationProfiles = [] }
            invalidateIntegrationPlan()
        }
    }
    func inspectIntegration() {
        guard stateReady, !setupBusy else { return }
        var args = ["integration-inspect", "--home", integrationHome]
        if !integrationProfile.isEmpty { args += ["--profile", integrationProfile] }
        setupCommand(args) { result in
            self.integrationProfiles = result["profiles"] as? [String] ?? []
            if let project = result["output_resume_project"] as? String { self.integrationOutputResumeProject = project }
            if let enabled = result["output_resume"] as? Bool { self.integrationOutputResume = enabled }
            self.integrationAsi1Effort = result["asi1_effort"] as? Bool ?? false
            if let enabled = result["delegate"] as? Bool { self.integrationDelegate = enabled }
            self.integrationCodexWorkspaceWrite = result["codex_workspace_write"] as? Bool ?? false
            if let role = result["agent_role"] as? String { self.integrationAgentRole = role }
            if self.integrationProject.isEmpty, let project = result["project"] as? String { self.integrationProject = project }
            self.integrationResult = result
            self.integrationPlan = result["pending_plan"] as? [String: Any]
            self.integrationAcknowledged = false
            self.integrationMessage = result["message"] as? String ?? "Inspection non confirmée."
            if let diagnostic = result["diagnostic"] as? String { self.integrationMessage += "\n" + diagnostic }
        }
    }
    func previewIntegration(action: String) {
        guard stateReady, !setupBusy else { return }
        var args = ["integration-plan", "--home", integrationHome, "--profile", integrationProfile, "--project", integrationProject, "--action", action]
        if integrationDelegate { args.append("--delegate") }
        if integrationDelegate && integrationCodexWorkspaceWrite { args.append("--codex-workspace-write") }
        if integrationOutputResume {
            args.append("--output-resume")
            if !integrationOutputResumeProject.isEmpty { args += ["--output-resume-project", integrationOutputResumeProject] }
        }
        if integrationAsi1Effort { args.append("--asi1-effort") }
        args += ["--agent-role", integrationAgentRole]
        setupCommand(args) { result in
            self.integrationPlan = result["plan"] as? [String: Any]
            self.integrationAcknowledged = false
            self.integrationResult = result
            self.integrationVerification = nil
            self.integrationMessage = result["message"] as? String ?? "Plan indisponible."
            if let diagnostic = result["diagnostic"] as? String { self.integrationMessage += "\n" + diagnostic }
        }
    }
    func applyIntegration() {
        guard stateReady, !setupBusy, integrationAcknowledged, let plan = integrationPlan else { return }
        setupCommand(["integration-apply"], input: ["plan": plan]) { result in
            self.integrationResult = result
            self.integrationAcknowledged = false
            self.integrationVerification = nil
            if let pending = result["pending_plan"] as? [String: Any] { self.integrationPlan = pending }
            else if result["status"] as? String == "completed" { self.integrationPlan = nil }
            self.integrationMessage = result["message"] as? String ?? "Application non confirmée."
            if let diagnostic = result["diagnostic"] as? String { self.integrationMessage += "\n" + diagnostic }
        }
    }
    func verifyIntegration() {
        guard stateReady, !setupBusy else { return }
        setupCommand(["integration-verify", "--home", integrationHome, "--profile", integrationProfile]) { result in
            self.integrationVerification = result
            self.integrationMessage = result["message"] as? String ?? "Vérification non confirmée."
            if let diagnostic = result["diagnostic"] as? String { self.integrationMessage += "\n" + diagnostic }
        }
    }
}

struct Integrations: View {
    @ObservedObject var app: App
    var hasProfile: Bool { !app.integrationProfile.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty }
    func path(_ label: String, _ value: String) -> some View {
        VStack(alignment: .leading, spacing: 3) { Text(label).font(.caption).foregroundStyle(.secondary); Text(value.isEmpty ? "Non choisi" : value).textSelection(.enabled) }
    }
    func planDetail(_ value: Any) -> String {
        if let text = value as? String { return text }
        guard JSONSerialization.isValidJSONObject(value), let data = try? JSONSerialization.data(withJSONObject: value, options: [.prettyPrinted, .sortedKeys]), let text = String(data: data, encoding: .utf8) else { return String(describing: value) }
        return text
    }
    var body: some View {
        VStack(alignment: .leading, spacing: 18) {
            SettingsCard(title: "Profil Harness",
                         status: hasProfile ? "Profil : " + app.integrationProfile : "Aucun profil",
                         ok: hasProfile) {
                HStack {
                    path("Dossier Harness", app.integrationHome)
                    Spacer()
                    Button("Choisir le dossier…") { app.chooseIntegrationDirectory(project: false) }
                }
                HStack(spacing: 10) {
                    TextField("Profil Harness", text: $app.integrationProfile).frame(maxWidth: 260)
                    if !app.integrationProfiles.isEmpty {
                        Picker("Profils détectés", selection: $app.integrationProfile) {
                            ForEach(app.integrationProfiles, id: \.self) { profile in Text(profile).tag(profile) }
                        }.labelsHidden().frame(maxWidth: 220)
                    }
                    Spacer()
                    Button("Relire la configuration") { app.inspectIntegration() }
                }
                SettingsHint(
                    text: "Reprise lit ce dossier pour connaître votre configuration ; elle ne l’applique pas.",
                    detail: "L’inspection n’écrit rien et ne lance aucune inférence. Elle relève les profils présents, les outils déjà exposés et le dernier plan éventuellement en attente.")
            }
            SettingsCard(title: "Délégation vers un expert externe",
                         status: app.integrationDelegate ? "Exposée" : "Désactivée",
                         ok: true) {
                Toggle("Exposer les outils de délégation", isOn: $app.integrationDelegate)
                SettingsHint(
                    text: "Par défaut, seuls l’observation et les reçus sont exposés. La délégation peut consommer du quota.",
                    detail: "Autoriser l’exposition n’exécute aucune inférence : c’est le client qui décide d’appeler. Configurer un outil ne vaut pas autorisation de l’utiliser.")
                Toggle("Autoriser l’écriture dans le projet sélectionné", isOn: $app.integrationCodexWorkspaceWrite)
                    .disabled(!app.integrationDelegate)
                SettingsHint(
                    text: "Les revues restent en lecture seule ; seul le mode implémentation écrit.",
                    detail: "Le mode implémentation autorise les modifications et vérifications locales dans ce projet. Les approbations natives restent requises ; cette option n’autorise pas implicitement un push, une fusion ou un déploiement.")
                Toggle("Reprendre après une limite de tokens de sortie", isOn: $app.integrationOutputResume)
                if app.integrationOutputResume {
                    TextField("Projet de reprise (vide : projet du connecteur)", text: $app.integrationOutputResumeProject)
                }
                SettingsHint(
                    text: "Deux reprises consécutives au maximum.",
                    detail: "Une reprise poursuit le même chat après une limite de tokens de sortie confirmée. La pause automatique de Reprise reste prioritaire et s’applique d’abord.")
                Toggle("Forcer un effort de raisonnement bas pour asi1", isOn: $app.integrationAsi1Effort)
                SettingsHint(
                    text: "S’applique au profil asi1-orchestrator uniquement.",
                    detail: "Déclare asi1 comme modèle raisonneur, ajoute les champs de compatibilité et insère le greffon dans le preset asi1-orchestrator pour forcer reasoning_effort=low. La route asi-cloud/asi1 et le preset doivent déjà exister. Le modèle par défaut n’est pas modifié ; rechargez le profil Harness pour charger une nouvelle version du greffon.")
                HStack(spacing: 10) {
                    Text("Rôle de l’agent").font(.callout)
                    Picker("Rôle de l’agent", selection: $app.integrationAgentRole) {
                        Text("Standard").tag("standard")
                        Text("Coordinateur").tag("coordinator")
                    }.labelsHidden().frame(maxWidth: 200)
                    Spacer()
                }
                SettingsHint(
                    text: "Le rôle est un label que vous attribuez ; il ne change pas le modèle.",
                    detail: "Coordinateur désigne l’agent qui répartit le travail. Ce rôle est indépendant du modèle utilisé et n’accorde aucun droit de délégation supplémentaire.")
            }
            SettingsCard(title: "Clé Artificial Analysis",
                         status: app.aaNote.contains("empreinte") ? "Enregistrée" : "Non vérifiée",
                         ok: app.aaNote.contains("empreinte")) {
                HStack {
                    SecureField("Clé (masquée)", text: $app.aaKey)
                    Button("Enregistrer") { app.saveAAKey() }.disabled(app.setupBusy || app.aaKey.trimmingCharacters(in: .whitespaces).isEmpty)
                }
                if !app.aaNote.isEmpty { Text(app.aaNote).font(.caption).foregroundStyle(.secondary).textSelection(.enabled) }
                SettingsHint(
                    text: "Écrite hors dépôt, permissions 0600, sauvegarde avant écrasement.",
                    detail: "Emplacement : REPRISE_SUPPORT/credentials/artificial-analysis.key. La clé est vérifiée par empreinte SHA-256 et n’est jamais affichée ni journalisée.")
            }
            SchedulerService(app: app)
            SettingsCard(title: "Connecteur Claude Desktop",
                         status: app.claudeIntegrationResult["status"] as? String == "completed" ? "Installé" : "État inconnu",
                         ok: app.claudeIntegrationResult["status"] as? String == "completed") {
                HStack {
                    path("Projet autorisé", app.integrationProject)
                    Spacer()
                    Button("Choisir le projet…") { app.chooseIntegrationDirectory(project: true) }
                }
                HStack(spacing: 10) {
                    Button("Relire l’état") { app.inspectClaudeIntegration() }
                    Button("Installer ou mettre à jour…") { app.previewClaudeIntegration("install") }.disabled(app.integrationProject.isEmpty)
                    Button("Retirer…") { app.previewClaudeIntegration("remove") }.disabled(app.integrationProject.isEmpty)
                    Button("Vérifier") { app.verifyClaudeIntegration() }
                }
                SettingsHint(
                    text: "Les autorisations se donnent dans Claude, pas ici.",
                    detail: "Un seul connecteur suffit : il reçoit les tâches, rend les résultats et demande une expertise aux autres harness configurés. L’exécution Python est incluse ; aucun Python système n’est requis. Dans Claude, autorisez les outils du connecteur reprise-harness. Reprise ne peut pas accorder ces autorisations à votre place et n’arrête pas Claude : la nouvelle configuration y est chargée après un rechargement. Une vérification de version ne prouve pas le chargement dans Claude ; le déclenchement Quick Apply reste une capacité distincte.",
                    detailLabel: "Autorisations et limites")
                Toggle("Remplacer le connecteur Reprise déjà reconnu (sauvegardé)", isOn: $app.claudeReplaceProbe)
                if let plan = app.claudeIntegrationPlan {
                    SettingsHint(text: "Un plan est prêt ; il attend votre accord.",
                                 detail: planDetail(plan), detailLabel: "Plan exact du connecteur")
                    Toggle("J’ai vérifié le projet et les changements du connecteur", isOn: $app.claudeIntegrationAcknowledged)
                    Button("Appliquer le plan") { app.applyClaudeIntegration() }.disabled(!app.claudeIntegrationAcknowledged)
                }
                if !app.claudeIntegrationResult.isEmpty {
                    SettingsHint(text: app.claudeIntegrationResult["message"] as? String ?? "Dernier résultat du connecteur.",
                                 detail: planDetail(app.claudeIntegrationResult), detailLabel: "Réponse complète")
                }
            }.disabled(app.setupBusy)
            SettingsCard(title: "Reprise après quota Claude Desktop",
                         status: app.claudeQuotaState["enabled"] as? Bool == true ? "Active" : "En pause",
                         ok: true) {
                Toggle("Poursuivre le chat après le renouvellement gratuit", isOn: Binding(
                    get: { app.claudeQuotaState["enabled"] as? Bool ?? true },
                    set: { app.configureClaudeQuota($0) }))
                Picker("Canal de reprise", selection: Binding(
                    get: { app.claudeQuotaState["transport"] as? String ?? "quick-apply" },
                    set: { app.configureClaudeQuota(app.claudeQuotaState["enabled"] as? Bool ?? true, transport: $0) })) {
                    Text("Quick Apply (claude-desktopd)").tag("quick-apply")
                    Text("Accessibilité macOS (autorisation requise)").tag("accessibility")
                }
                SettingsHint(
                    text: "La pause globale reste prioritaire.",
                    detail: "Quick Apply passe par le même service que les autres harness et exige une capacité d’émission vérifiée. L’autre canal demande l’autorisation Accessibilité de macOS. Dans les deux cas, Reprise examine le dernier message du chat affiché et son bouton Continuer, sans saisir de texte à votre place.")
                if let events = app.claudeQuotaState["events"] as? [[String: Any]], let latest = events.first,
                   let due = latest["due"] as? Double {
                    HStack(spacing: 16) {
                        Text("Prochaine tentative : " + Date(timeIntervalSince1970: due).formatted(date: .abbreviated, time: .shortened))
                        Text("État : " + (latest["status"] as? String ?? "inconnu")).foregroundStyle(.secondary)
                    }.font(.caption)
                }
                if let observation = app.claudeQuotaState["observation"] as? [String: Any], let state = observation["status"] as? String {
                    Text("Observation : " + state).font(.caption).foregroundStyle(.secondary)
                }
                Button("Actualiser le suivi") { app.refreshClaudeQuota() }
            }.disabled(app.setupBusy).onAppear { app.refreshClaudeQuota() }
            if app.setupBusy { InlineStatus(busy: true, message: "Opération en cours…") }
            SettingsCard(title: "Appliquer la configuration",
                         status: app.integrationPlan == nil ? "Aucun plan en attente" : "Plan en attente",
                         ok: app.integrationPlan == nil) {
                HStack(spacing: 10) {
                    Button("Prévisualiser l’installation…") { app.previewIntegration(action: "install") }
                    Button("Prévisualiser le retrait…") { app.previewIntegration(action: "remove") }
                    Spacer()
                }.disabled(!hasProfile || app.integrationProject.isEmpty)
                if let plan = app.integrationPlan {
                    Divider()
                    HStack(spacing: 24) {
                        path("Action", plan["action"] as? String ?? "")
                        path("Profil", plan["profile"] as? String ?? "")
                    }
                    Text("Rôle : " + ((plan["agent_role"] as? String == "coordinator") ? "Coordinateur" : "Standard"))
                    SettingsHint(
                        text: "Les modifications ci-dessous sont exactes et réversibles.",
                        detail: planDetail((plan["changes"] as? [[String: Any]] ?? [])), detailLabel: "Fichiers avant / après")
                    if let project = plan["output_resume_project"] as? String { path("Projet de reprise", project) }
                    Text("Reprise après limite de sortie : " + ((plan["output_resume"] as? Bool == true) ? "autorisée" : "désactivée"))
                    Text("Effort ASI1 bas : " + ((plan["asi1_effort"] as? Bool == true) ? "installé" : "non installé"))
                    Text("Délégation : " + ((plan["delegate"] as? Bool == true) ? "exposée" : "non exposée"))
                    if let release = plan["release"], let state = plan["state"] {
                        SettingsHint(text: "Version et état utilisés par le connecteur.",
                                     detail: planDetail(release) + "\n\n" + planDetail(state), detailLabel: "Détail technique")
                    }
                    Text("Empreinte du plan : " + (plan["fingerprint"] as? String ?? "indisponible")).font(.caption)
                    Toggle("J’autorise les modifications de ce plan sur le profil affiché.", isOn: $app.integrationAcknowledged)
                    Button("Appliquer ce plan") { app.applyIntegration() }.disabled(!app.integrationAcknowledged)
                }
                if let result = app.integrationResult {
                    if let backup = result["backup"] as? String { path("Sauvegarde", backup) }
                    ForEach(result["backups"] as? [String] ?? [], id: \.self) { path("Sauvegarde", $0) }
                }
                Divider()
                Button("Vérifier la configuration sans inférence") { app.verifyIntegration() }.disabled(!hasProfile)
                if let verification = app.integrationVerification {
                    let levels = verification["levels"] as? [String: Any] ?? [:]
                    SettingsHint(
                        text: "Ce qui est prouvé, et ce qui reste à confirmer.",
                        detail: [
                            "Configuration écrite : " + ((levels["configuration_written"] as? Bool == true) ? "vérifiée" : "non confirmée"),
                            "Intégration chargée par Harness : inconnue",
                            "Outils découverts par le client : inconnu",
                            "Échange confirmé avec le client : inconnu",
                            (verification["bridge_tools"] as? [String]).map { "Outils annoncés : " + $0.joined(separator: ", ") } ?? "",
                            "Le test de version ne prouve pas le chargement par Harness ni un échange depuis le client. Le profil complet et ses surcharges restent à valider dans Harness."
                        ].filter { !$0.isEmpty }.joined(separator: "\n"),
                        detailLabel: "Bornes de preuve")
                }
            }
        }
        .disabled(app.setupBusy || !app.stateReady)
        .onChange(of: app.integrationProfile) { _, _ in app.invalidateIntegrationPlan() }
        .onChange(of: app.integrationOutputResumeProject) { _, _ in app.invalidateIntegrationPlan() }
        .onChange(of: app.integrationOutputResume) { _, _ in app.invalidateIntegrationPlan() }
        .onChange(of: app.integrationAgentRole) { _, _ in app.invalidateIntegrationPlan() }
        .onChange(of: app.integrationCodexWorkspaceWrite) { _, _ in app.invalidateIntegrationPlan() }
        .onChange(of: app.integrationDelegate) { _, _ in app.invalidateIntegrationPlan() }
    }
}
