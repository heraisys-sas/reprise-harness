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
        panel.message = project ? "Choisissez le projet accessible au pont Reprise." : "Choisissez le dossier de configuration Harness."
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
            ProxyService(app: app)
            Divider()
            SchedulerService(app: app)
            Text("L’installation écrit la configuration et les consignes du profil choisi. Elle ne démarre aucun service et n’effectue aucune inférence.").foregroundStyle(.secondary)
            VStack(alignment: .leading, spacing: 10) {
            Text("Cible explicite").font(.headline)
                VStack(alignment: .leading, spacing: 12) {
                    HStack { path("Dossier Harness", app.integrationHome); Spacer(); Button("Choisir Harness…") { app.chooseIntegrationDirectory(project: false) } }
                    HStack {
                        TextField("Profil Harness", text: $app.integrationProfile)
                        if !app.integrationProfiles.isEmpty {
                            Menu("Profils détectés") { ForEach(app.integrationProfiles, id: \.self) { profile in Button(profile) { app.integrationProfile = profile } } }
                        }
                    }
                    HStack { path("Projet du pont", app.integrationProject); Spacer(); Button("Choisir le projet…") { app.chooseIntegrationDirectory(project: true) } }
                    Toggle("Exposer aussi les outils de délégation", isOn: $app.integrationDelegate)
                    Toggle("Autoriser Codex à modifier le projet sélectionné", isOn: $app.integrationCodexWorkspaceWrite).disabled(!app.integrationDelegate)
                    Text("Le mode implémentation permet les modifications et vérifications locales dans ce projet. Les revues restent en lecture seule. Les approbations natives restent requises ; cette option n’autorise pas implicitement push, fusion ou déploiement.").font(.caption).foregroundStyle(.secondary)
                    Toggle("Continuer après une limite de tokens de sortie", isOn: $app.integrationOutputResume)
                    if app.integrationOutputResume { TextField("Projet de continuation (vide : projet du pont)", text: $app.integrationOutputResumeProject) }
                    Text("Autorise une continuation dans le même chat Harness, après max-tokens confirmé ; deux continuations consécutives maximum. La pause automatique de Reprise reste prioritaire.").font(.caption).foregroundStyle(.secondary)
                    Toggle("Effort bas pour le mode asi1-orchestrator", isOn: $app.integrationAsi1Effort)
                    Text("Déclare asi1 raisonneur, ajoute les champs compat et insère le plugin dans le preset asi1-orchestrator pour forcer reasoning_effort=low. La route asi-cloud/asi1 et le preset doivent déjà exister. Ne change pas le modèle par défaut ; recharger le profil Harness pour charger une nouvelle version du plugin.").font(.caption).foregroundStyle(.secondary)
                    Picker("Rôle de l’agent", selection: $app.integrationAgentRole) {
                        Text("Standard").tag("standard")
                        Text("Coordinateur").tag("coordinator")
                    }
                    Text("Coordinateur : rôle attribué par vos soins, indépendant du modèle. N’accorde aucun droit de délégation supplémentaire.").font(.caption).foregroundStyle(.secondary)
                    Text("Par défaut : outils d’observation et reçus. Activer la délégation autorise leur exposition au client ; la délégation peut consommer du quota. Activer la configuration ne vaut pas autorisation d’inférence.").font(.caption).foregroundStyle(.secondary)
                    Button("Inspecter") { app.inspectIntegration() }
                }.padding(6)
            }
            GroupBox("Connecteur reprise-harness · Claude Desktop") {
                VStack(alignment: .leading, spacing: 8) {
                    path("Projet autorisé", app.integrationProject)
                    Button("Choisir le projet Claude…") { app.chooseIntegrationDirectory(project: true) }
                    Text("Un seul connecteur : recevoir les tâches, rendre les résultats et demander une expertise aux autres harness configurés. Runtime inclus ; aucun Python système requis.").font(.caption).foregroundStyle(.secondary)
                    Toggle("Remplacer l’ancien probe Reprise reconnu (sauvegardé)", isOn: $app.claudeReplaceProbe)
                    HStack {
                        Button("Inspecter") { app.inspectClaudeIntegration() }
                        Button("Installer / mettre à jour…") { app.previewClaudeIntegration("install") }.disabled(app.integrationProject.isEmpty)
                        Button("Retirer…") { app.previewClaudeIntegration("remove") }.disabled(app.integrationProject.isEmpty)
                        Button("Vérifier") { app.verifyClaudeIntegration() }
                    }
                    if let plan = app.claudeIntegrationPlan {
                        Text(planDetail(plan)).font(.caption.monospaced()).textSelection(.enabled)
                        Toggle("J’ai vérifié le projet et les changements du connecteur Claude", isOn: $app.claudeIntegrationAcknowledged)
                        Button("Appliquer le plan Claude") { app.applyClaudeIntegration() }.disabled(!app.claudeIntegrationAcknowledged)
                    }
                    if !app.claudeIntegrationResult.isEmpty { Text(planDetail(app.claudeIntegrationResult)).font(.caption).textSelection(.enabled) }
                    Text("Dans Claude, autorisez les outils du connecteur reprise-harness pour recevoir les tâches, rendre les résultats et déléguer. Reprise ne peut pas accorder ces autorisations à votre place. Claude devra recharger sa configuration. Reprise ne l’arrête pas automatiquement. Une sonde de release ne prouve pas le chargement dans Claude ; le déclenchement Quick Apply reste une capacité distincte.").font(.caption).foregroundStyle(.secondary)
                }.padding(6)
            }.disabled(app.setupBusy)
            GroupBox("Reprise après quota Claude Desktop") {
                VStack(alignment: .leading, spacing: 8) {
                    Toggle("Continuer le chat après le reset gratuit", isOn: Binding(
                        get: { app.claudeQuotaState["enabled"] as? Bool ?? true },
                        set: { app.configureClaudeQuota($0) }))
                    Picker("Canal de continuation", selection: Binding(
                        get: { app.claudeQuotaState["transport"] as? String ?? "quick-apply" },
                        set: { app.configureClaudeQuota(app.claudeQuotaState["enabled"] as? Bool ?? true, transport: $0) })) {
                        Text("Quick Apply · claude-desktopd").tag("quick-apply")
                        Text("Accessibilité macOS · option explicite").tag("accessibility")
                    }
                    Text("Quick Apply utilise le même démon que les autres harness et exige une capacité d’émission vérifiée. L’autre canal requiert l’autorisation Accessibilité macOS. Reprise vérifie le dernier message du chat affiché et son bouton Continuer, sans saisir de prompt. La pause globale reste prioritaire.").font(.caption).foregroundStyle(.secondary)
                    if let events = app.claudeQuotaState["events"] as? [[String: Any]], let latest = events.first,
                       let due = latest["due"] as? Double {
                        Text("Échéance : " + Date(timeIntervalSince1970: due).formatted(date: .abbreviated, time: .shortened))
                        Text("État : " + (latest["status"] as? String ?? "inconnu"))
                    }
                    if let observation = app.claudeQuotaState["observation"] as? [String: Any], let state = observation["status"] as? String {
                        Text("Observation : " + state).font(.caption).foregroundStyle(.secondary)
                    }
                    Button("Actualiser le suivi Claude") { app.refreshClaudeQuota() }
                }.padding(6)
            }.disabled(app.setupBusy).onAppear { app.refreshClaudeQuota() }
            Text(app.integrationMessage).textSelection(.enabled)
            if app.setupBusy { ProgressView("Opération en cours…") }
            HStack {
                Button("Prévisualiser installation ou réparation") { app.previewIntegration(action: "install") }
                Button("Prévisualiser le retrait") { app.previewIntegration(action: "remove") }
            }.disabled(!hasProfile || app.integrationProject.isEmpty)
            if let plan = app.integrationPlan {
                VStack(alignment: .leading, spacing: 10) {
            Text("Modifications prévues ou à reprendre").font(.headline)
                    VStack(alignment: .leading, spacing: 10) {
                        path("Action", plan["action"] as? String ?? "")
                        path("Dossier Harness du plan", plan["home"] as? String ?? "")
                        path("Profil du plan", plan["profile"] as? String ?? "")
                        path("Projet du plan", plan["project"] as? String ?? "")
                        Text("Rôle : " + ((plan["agent_role"] as? String == "coordinator") ? "Coordinateur" : "Standard"))
                        if let project = plan["output_resume_project"] as? String { path("Projet de continuation", project) }
                        Text("Continuation après limite de sortie : " + ((plan["output_resume"] as? Bool == true) ? "autorisée" : "désactivée"))
                        Text("Effort ASI1 bas : " + ((plan["asi1_effort"] as? Bool == true) ? "installé" : "non installé"))
                        Text("Délégation : " + ((plan["delegate"] as? Bool == true) ? "exposée" : "non exposée"))
                        ForEach(Array((plan["changes"] as? [[String: Any]] ?? []).enumerated()), id: \.offset) { _, change in
                            VStack(alignment: .leading, spacing: 3) {
                                Text((change["action"] as? String ?? "Modification") + " : " + (change["path"] as? String ?? "Chemin inconnu")).bold()
                                Text("Avant : " + (change["before_sha256"] as? String ?? "absent"))
                                Text("Après : " + (change["after_sha256"] as? String ?? "absent"))
                            }.font(.caption)
                        }
                        if let release = plan["release"] { DisclosureGroup("Version et chemins du bundle") { Text(planDetail(release)).font(.caption).frame(maxWidth: .infinity, alignment: .leading) } }
                        if let state = plan["state"] { DisclosureGroup("État utilisé par le pont") { Text(planDetail(state)).font(.caption).frame(maxWidth: .infinity, alignment: .leading) } }
                        Text("Empreinte : " + (plan["fingerprint"] as? String ?? "Indisponible")).font(.caption)
                        Toggle("J’autorise explicitement les modifications de ce plan sur le profil affiché.", isOn: $app.integrationAcknowledged)
                        Button("Appliquer ou reprendre ce plan") { app.applyIntegration() }.disabled(!app.integrationAcknowledged)
                    }.padding(6).frame(maxWidth: .infinity, alignment: .leading).textSelection(.enabled)
                }
            }
            if let result = app.integrationResult {
                if let backup = result["backup"] as? String { path("Sauvegarde", backup) }
                ForEach(result["backups"] as? [String] ?? [], id: \.self) { path("Sauvegarde", $0) }
            }
            Button("Vérifier la configuration et sonder le pont sans inférence") { app.verifyIntegration() }.disabled(!hasProfile)
            if let verification = app.integrationVerification {
                VStack(alignment: .leading, spacing: 10) {
            Text("Bornes de preuve").font(.headline)
                    let levels = verification["levels"] as? [String: Any] ?? [:]
                    VStack(alignment: .leading, spacing: 8) {
                        Text("Configuration écrite : " + ((levels["configuration_written"] as? Bool == true) ? "vérifiée" : "non confirmée"))
                        Text("Intégration chargée par Harness : inconnue")
                        Text("Outils découverts par le client : inconnu")
                        Text("Échange confirmé avec le client : inconnu")
                        if let tools = verification["bridge_tools"] as? [String], !tools.isEmpty { Text("Outils annoncés par la sonde du pont : " + tools.joined(separator: ", ")) }
                        Text("La sonde du pont ne prouve pas le chargement par Harness ni un échange depuis le client.").font(.caption).foregroundStyle(.secondary)
                        Text("Le profil complet et ses éventuelles surcharges restent à valider dans Harness.").font(.caption).foregroundStyle(.secondary)
                    }.padding(6).frame(maxWidth: .infinity, alignment: .leading).textSelection(.enabled)
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
