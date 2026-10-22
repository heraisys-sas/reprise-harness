import Cocoa
import SwiftUI

extension App {
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
                    Text("Par défaut : outils d’observation et reçus. Activer la délégation autorise leur exposition au client ; la délégation peut consommer du quota. Activer la configuration ne vaut pas autorisation d’inférence.").font(.caption).foregroundStyle(.secondary)
                    Button("Inspecter") { app.inspectIntegration() }
                }.padding(6)
            }
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
        .onChange(of: app.integrationDelegate) { _, _ in app.invalidateIntegrationPlan() }
    }
}
