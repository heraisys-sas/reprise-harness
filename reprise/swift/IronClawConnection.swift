import SwiftUI

extension App {
    func inspectIronClaw() {
        setupCommand(["ironclaw-inspect"]) { result in
            guard let endpoint = result["endpoint"] as? [String: Any] else { return }
            self.ironclawHost = endpoint["host"] as? String ?? ""
            self.ironclawUser = endpoint["user"] as? String ?? ""
            self.ironclawPort = String(endpoint["port"] as? Int ?? 22)
            self.ironclawHTTPPort = String(endpoint["http_port"] as? Int ?? 3000)
            self.ironclawIdentity = endpoint["identity_file"] as? String ?? ""
            self.ironclawTokenFile = endpoint["token_url_file"] as? String ?? ""
        }
    }
    func configureIronClaw() {
        guard let port = Int(ironclawPort), let httpPort = Int(ironclawHTTPPort) else {
            ironclawNote = "Les ports doivent être des nombres entiers."
            return
        }
        setupCommand(["ironclaw-configure"], input: ["host": ironclawHost, "user": ironclawUser,
            "port": port, "http_port": httpPort, "identity_file": ironclawIdentity,
            "token_url_file": ironclawTokenFile]) { result in
            self.ironclawNote = result["message"] as? String ?? "Enregistrement non confirmé."
        }
    }
    func probeIronClaw() {
        setupCommand(["ironclaw-probe"]) { result in
            self.ironclawNote = result["message"] as? String ?? "Connexion non confirmée."
        }
    }
}

struct IronClawConnection: View {
    @ObservedObject var app: App
    var body: some View {
        GroupBox("IronClaw distant par SSH direct") {
            VStack(alignment: .leading, spacing: 12) {
                Text("La sonde vérifie SSH et l’API authentifiée dans le conteneur. Elle n’ouvre pas de tunnel, ne démarre aucun service distant et ne lance aucune tâche.").foregroundStyle(.secondary)
                TextField("Hôte SSH", text: $app.ironclawHost)
                TextField("Utilisateur SSH", text: $app.ironclawUser)
                HStack {
                    TextField("Port SSH", text: $app.ironclawPort)
                    TextField("Port HTTP dans le conteneur", text: $app.ironclawHTTPPort)
                }
                TextField("Chemin absolu de la clé privée existante", text: $app.ironclawIdentity)
                TextField("Chemin du fichier URL privé contenant le jeton", text: $app.ironclawTokenFile)
                Text("Les fichiers restent à leur emplacement. L’hôte doit déjà être approuvé dans known_hosts. Le fichier URL doit appartenir à votre compte et être privé (0600).").font(.caption).foregroundStyle(.secondary)
                HStack {
                    Button("Enregistrer les paramètres") { app.configureIronClaw() }
                    Button("Vérifier la connexion enregistrée") { app.probeIronClaw() }
                }
                Text(app.ironclawNote)
                Text("Soumission et récupération durable des tâches : non activées. La vérification de connexion ne démontre pas leur fonctionnement.").font(.caption).foregroundStyle(.secondary)
            }.padding(6)
        }
        .disabled(app.setupBusy || !app.stateReady)
        .onAppear { app.inspectIronClaw() }
    }
}
