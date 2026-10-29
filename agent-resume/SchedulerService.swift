import SwiftUI

extension App {
    func inspectSchedulerService() {
        guard stateReady, !setupBusy else { return }
        setupCommand(["scheduler-service-inspect"]) { result in
            self.schedulerService = result
            self.schedulerServicePlan = result["pending_plan"] as? [String: Any]
            self.schedulerServiceAcknowledged = false
            self.schedulerServiceMessage = result["message"] as? String ?? "Inspection non confirmée."
        }
    }
    func planSchedulerService(_ action: String) {
        guard stateReady, !setupBusy else { return }
        var args = ["scheduler-service-plan", "--action", action]
        if schedulerServiceActivate && action == "install" { args.append("--activate") }
        setupCommand(args) { result in
            self.schedulerServicePlan = result["plan"] as? [String: Any]
            self.schedulerServiceAcknowledged = false
            self.schedulerServiceMessage = result["message"] as? String ?? "Plan indisponible."
        }
    }
    func applySchedulerService() {
        guard stateReady, !setupBusy, schedulerServiceAcknowledged, let plan = schedulerServicePlan else { return }
        setupCommand(["scheduler-service-apply"], input: ["plan": plan]) { result in
            self.schedulerServiceAcknowledged = false
            self.schedulerServiceMessage = result["message"] as? String ?? "Application non confirmée."
            if let backup = result["backup"] as? String { self.schedulerServiceMessage += "\nSauvegarde : " + backup }
            if result["status"] as? String == "completed" { self.schedulerServicePlan = nil }
        }
    }
}
struct SchedulerService: View {
    @ObservedObject var app: App
    func flag(_ key: String) -> String {
        guard let value = app.schedulerService[key] as? Bool else { return "inconnu" }
        return value ? "oui" : "non"
    }
    func detail(_ value: Any) -> String {
        guard JSONSerialization.isValidJSONObject(value), let data = try? JSONSerialization.data(withJSONObject: value, options: [.prettyPrinted, .sortedKeys]), let text = String(data: data, encoding: .utf8) else { return String(describing: value) }
        return text
    }
    var loaded: Bool { app.schedulerService["loaded"] as? Bool == true }
    var body: some View {
        SettingsCard(title: "Planificateur utilisateur",
                     status: app.schedulerService.isEmpty ? "Non inspecté" : (loaded ? "Chargé" : "Arrêté"),
                     ok: loaded) {
            if app.schedulerService.isEmpty {
                Text("Inspectez le service pour connaître son état.").font(.callout).foregroundStyle(.secondary)
            } else {
                HStack(spacing: 16) {
                    Text("Configuration préparée : " + flag("state_prepared"))
                    Text("Fichier du service : " + flag("file_present"))
                    Text("En attente : " + (String(describing: app.schedulerService["pending"] as? Int ?? 0)))
                }.font(.caption).foregroundStyle(.secondary)
                if let tick = app.schedulerService["last_tick"] as? Double {
                    Text("Dernier tick : " + Dashboard.localDateTime.string(from: Date(timeIntervalSince1970: tick)))
                        .font(.caption).foregroundStyle(.secondary)
                } else {
                    Label("Aucun tick observé : vérifiez le chargement, puis préparez une réparation si nécessaire.",
                          systemImage: "exclamationmark.triangle").font(.caption).foregroundStyle(.orange)
                }
                if let error = app.schedulerService["last_error"] as? String {
                    Label(error, systemImage: "xmark.octagon").font(.caption).foregroundStyle(.orange)
                }
                SettingsHint(
                    text: "Un service chargé ne prouve pas qu’un travail a réussi.",
                    detail: ["Release configuré à jour : " + flag("configured_current"),
                             "Release chargé à jour : " + flag("loaded_current"),
                             "Registre configuré conforme : " + flag("configured_state_matches"),
                             "Registre chargé conforme : " + flag("loaded_state_matches"),
                             "Exécution et outils intègres : " + flag("runtime_integrity"),
                             "Automatique actif : " + flag("auto_enabled"),
                             (app.schedulerService["last_exit"] as? String).map { "Dernière sortie launchd : " + $0 } ?? "",
                             "Un service chargé ne prouve ni un tick réussi ni une reprise autorisée."]
                        .filter { !$0.isEmpty }.joined(separator: "\n")
                        + "\n\n" + detail(app.schedulerService),
                    detailLabel: "Preuves du chargement")
            }
            HStack(spacing: 10) {
                Button("Inspecter") { app.inspectSchedulerService() }
                Button("Installer ou réparer…") { app.planSchedulerService("install") }
                Button("Retirer…") { app.planSchedulerService("remove") }
                Spacer()
            }
            Toggle("Activer le service après installation", isOn: $app.schedulerServiceActivate)
            SettingsHint(
                text: "Sans activation, le service installé reste arrêté.",
                detail: "Lors d’un remplacement, le service chargé est déchargé. Si l’activation n’est pas cochée, il reste arrêté : une installation ne lance donc aucun travail. Une pause automatique existante est conservée et sa réactivation se fait séparément dans Reprises.")
            if !app.schedulerServiceMessage.isEmpty { InlineStatus(busy: false, message: app.schedulerServiceMessage) }
            if let plan = app.schedulerServicePlan {
                Divider()
                SettingsHint(text: "Un plan attend votre accord.",
                             detail: detail(plan), detailLabel: "Plan exact")
                Toggle("J’autorise les modifications et l’activation de ce plan.", isOn: $app.schedulerServiceAcknowledged)
                Button("Appliquer ce plan") { app.applySchedulerService() }.disabled(!app.schedulerServiceAcknowledged)
            }
        }
        .disabled(app.setupBusy || !app.stateReady)
        .onChange(of: app.schedulerServiceActivate) { _, _ in app.schedulerServicePlan = nil; app.schedulerServiceAcknowledged = false }
    }
}
