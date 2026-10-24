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
    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("Planificateur utilisateur Reprise").font(.headline)
            VStack(alignment: .leading, spacing: 10) {
                Text("Installez ou réparez le service de reprise indépendant du clone. L’activation peut exécuter les travaux déjà autorisés selon le mode automatique enregistré.").font(.caption).foregroundStyle(.secondary)
                HStack {
                    Button("Inspecter / vérifier") { app.inspectSchedulerService() }
                    Button("Installation / réparation / mise à jour…") { app.planSchedulerService("install") }
                    Button("Retrait du service…") { app.planSchedulerService("remove") }
                }
                Toggle("Activer explicitement le service après installation", isOn: $app.schedulerServiceActivate)
                Text("Lors d’un remplacement, le service chargé est déchargé ; sans activation cochée, il reste arrêté. Une pause automatique existante est conservée. Sa réactivation se fait séparément dans Reprises.").font(.caption).foregroundStyle(.secondary)
                if !app.schedulerService.isEmpty {
                    Text("État préparé : " + flag("state_prepared") + " · Plist présent : " + flag("file_present") + " · Chargé : " + flag("loaded"))
                    Text("Release configuré à jour : " + flag("configured_current") + " · Release chargé à jour : " + flag("loaded_current"))
                    Text("Registre configuré conforme : " + flag("configured_state_matches") + " · Registre chargé conforme : " + flag("loaded_state_matches"))
                    Text("Runtime/helpers intègres : " + flag("runtime_integrity") + " · Automatique actif : " + flag("auto_enabled"))
                    if let tick = app.schedulerService["last_tick"] as? Double { Text("Dernier tick : " + Dashboard.localDateTime.string(from: Date(timeIntervalSince1970: tick))) }
                    else { Text("Aucun tick observé : inspectez le chargement, le runtime et la pause ; préparez une réparation si nécessaire.").foregroundStyle(.orange) }
                    if let exit = app.schedulerService["last_exit"] as? String { Text("Dernière sortie launchd : " + exit) }
                    Text("Un service chargé ne prouve pas un tick réussi ni une reprise autorisée.").font(.caption).foregroundStyle(.secondary)
                    if let error = app.schedulerService["last_error"] as? String { Text("Dernière erreur : " + error) }
                    if let count = app.schedulerService["pending"] as? Int { Text("Travaux en attente : \(count)") }
                    DisclosureGroup("Chemins et preuves du chargement") { Text(detail(app.schedulerService)).font(.caption).textSelection(.enabled) }
                }
                Text(app.schedulerServiceMessage).textSelection(.enabled)
                if let plan = app.schedulerServicePlan {
                    DisclosureGroup("Plan exact, release, intervalle et restauration") { Text(detail(plan)).font(.caption).textSelection(.enabled) }
                    Toggle("J’autorise les modifications et l’activation indiquées dans ce plan.", isOn: $app.schedulerServiceAcknowledged)
                    Button("Appliquer ou reprendre le plan") { app.applySchedulerService() }.disabled(!app.schedulerServiceAcknowledged)
                }
            }.padding(6).frame(maxWidth: .infinity, alignment: .leading)
        }
        .disabled(app.setupBusy || !app.stateReady)
        .onChange(of: app.schedulerServiceActivate) { _, _ in app.schedulerServicePlan = nil; app.schedulerServiceAcknowledged = false }
    }
}
