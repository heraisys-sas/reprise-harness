import Cocoa
import SwiftUI

extension App {
    // Dedicated preparation channel: no normal refresh, inference or service startup.
    func setupCommand(_ args: [String], input: [String: Any]? = nil, completion: @escaping ([String: Any]) -> Void) {
        guard !setupBusy else { return }
        if let active = setupProcess, active.isRunning {
            setupMessage = "L’opération précédente est encore active. Attendez sa fin puis réessayez."
            return
        }
        guard runtimeError == nil else { setupMessage = runtimeError!; return }
        setupBusy = true
        DispatchQueue.global(qos: .utility).async {
            let process = Process()
            let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
            var result: [String: Any] = [:]
            do {
                try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true, attributes: [.posixPermissions: 0o700])
                let out = directory.appendingPathComponent("out"), err = directory.appendingPathComponent("err"), incoming = directory.appendingPathComponent("in")
                FileManager.default.createFile(atPath: out.path, contents: nil)
                FileManager.default.createFile(atPath: err.path, contents: nil)
                try (input.map { try JSONSerialization.data(withJSONObject: $0) } ?? Data()).write(to: incoming)
                let stdout = try FileHandle(forWritingTo: out), stderr = try FileHandle(forWritingTo: err), stdin = try FileHandle(forReadingFrom: incoming)
                defer { try? stdout.close(); try? stderr.close(); try? stdin.close() }
                process.executableURL = URL(fileURLWithPath: self.python)
                process.arguments = (self.python == "/usr/bin/env" ? ["python3"] : []) + (self.usesBundledRuntime ? ["-E", "-s", "-B"] : []) + [self.root.appendingPathComponent("resume").path] + args
                var environment = ProcessInfo.processInfo.environment
                for key in ["PYTHONHOME", "PYTHONPATH", "PYTHONUSERBASE", "PYTHONSTARTUP"] { environment.removeValue(forKey: key) }
                environment["PYTHONNOUSERSITE"] = "1"; environment["PYTHONDONTWRITEBYTECODE"] = "1"
                process.environment = environment
                process.standardOutput = stdout; process.standardError = stderr; process.standardInput = stdin
                DispatchQueue.main.sync { self.setupProcess = process }
                try process.run()
                let deadline = Date().addingTimeInterval(300)
                while process.isRunning && Date() < deadline { Thread.sleep(forTimeInterval: 0.1) }
                if process.isRunning {
                    process.terminate()
                    let stopDeadline = Date().addingTimeInterval(5)
                    while process.isRunning && Date() < stopDeadline { Thread.sleep(forTimeInterval: 0.1) }
                    result = ["status": "failed", "message": "Délai dépassé. L’état sera revérifié avant toute reprise.", "timed_out": true]
                } else {
                    let data = try Data(contentsOf: out)
                    result = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any] ?? ["status": "failed", "message": "Réponse de préparation illisible."]
                }
                let diagnostic = String(data: (try? Data(contentsOf: err)) ?? Data(), encoding: .utf8) ?? ""
                if !diagnostic.isEmpty && result["status"] as? String != "completed" { result["diagnostic"] = String(diagnostic.prefix(2000)) }
            } catch { result = ["status": "failed", "message": error.localizedDescription] }
            if !process.isRunning { try? FileManager.default.removeItem(at: directory) }
            DispatchQueue.main.async {
                self.setupBusy = false
                if !process.isRunning { self.setupProcess = nil }
                completion(result)
            }
        }
    }
    func checkState(afterOperation: Bool = false) {
        setupCommand(["state-status"]) { result in
            self.setupStatus = result
            if let pending = result["pending_plan"] as? [String: Any] { self.setupPlan = pending }
            if afterOperation {
                if result["status"] as? String != "ready" { self.setupMessage += "\n" + (result["message"] as? String ?? "État non prêt.") }
            } else if result["status"] as? String == "ready" {
                self.stateReady = true
                self.refresh()
            } else { self.setupMessage = result["message"] as? String ?? "Préparation nécessaire. Réessayez après vérification du runtime." }
        }
    }
    func selectSetupSource() {
        let panel = NSOpenPanel()
        panel.canChooseFiles = false; panel.canChooseDirectories = true; panel.allowsMultipleSelection = false
        panel.message = "Choisissez le dossier d’état à importer."
        if panel.runModal() == .OK, let url = panel.url {
            setupSource = url.path; setupPlan = nil; setupReceipt = nil; setupSourceStopped = false
        }
    }
    func previewSetup() {
        setupConfirmNew = false; setupSourceStopped = false
        guard let target = setupStatus["target"] as? String, let quick = setupStatus["quick_target"] as? String else { return }
        var args = ["state-plan", "--operation", setupOperation, "--target", target, "--quick-target", quick]
        if setupOperation == "import" { args += ["--source", setupSource] }
        setupCommand(args) { result in
            self.setupPlan = result["plan"] as? [String: Any]
            self.setupReceipt = nil
            self.setupMessage = result["message"] as? String ?? "Prévisualisation indisponible."
            if let diagnostic = result["diagnostic"] as? String { self.setupMessage += "\n" + diagnostic }
        }
    }
    func applySetup() {
        guard let plan = setupPlan else { return }
        let payload: [String: Any] = ["plan": plan, "confirm_new": setupConfirmNew, "source_stopped": setupSourceStopped]
        setupCommand(["state-apply"], input: payload) { result in
            self.setupReceipt = result
            self.setupMessage = result["message"] as? String ?? "Résultat non confirmé."
            if let diagnostic = result["diagnostic"] as? String { self.setupMessage += "\n" + diagnostic }
            self.setupConfirmNew = false; self.setupSourceStopped = false
            self.checkState(afterOperation: true)
        }
    }
    func finishSetup() {
        guard setupStatus["status"] as? String == "ready", !setupBusy else { return }
        startupAttempted = true // Preparation never replays saved service-start preferences.
        stateReady = true
        refresh()
    }
}

struct StateSetup: View {
    @ObservedObject var app: App
    var pending: Bool { app.setupStatus["status"] as? String == "pending" }
    var operation: String { app.setupPlan?["operation"] as? String ?? app.setupOperation }
    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 18) {
                Label("Préparer Reprise", systemImage: "externaldrive.badge.checkmark").font(.largeTitle.bold())
                Text("Choisissez explicitement un nouvel état ou importez votre état existant. Les services doivent être arrêtés avant toute écriture ; Reprise ne les arrête pas.")
                Text(app.setupMessage).textSelection(.enabled).accessibilityLabel("État de préparation : " + app.setupMessage)
                if app.setupBusy { ProgressView("Vérification en cours…") }
                GroupBox("Destinations") {
                    VStack(alignment: .leading) {
                        Text("Reprise : " + (app.setupStatus["target"] as? String ?? "À vérifier"))
                        Text("Quick Apply : " + (app.setupStatus["quick_target"] as? String ?? "À vérifier"))
                    }.frame(maxWidth: .infinity, alignment: .leading).textSelection(.enabled)
                }
                if app.setupStatus["status"] as? String != "ready" {
                    if !pending {
                        Picker("Préparation", selection: $app.setupOperation) {
                            Text("Importer un état existant").tag("import")
                            Text("Créer un nouvel état").tag("initialize")
                        }.pickerStyle(.radioGroup).onChange(of: app.setupOperation) { _, _ in app.setupPlan = nil; app.setupReceipt = nil; app.setupConfirmNew = false; app.setupSourceStopped = false }
                        if app.setupOperation == "import" {
                            HStack { Button("Choisir le dossier source…") { app.selectSetupSource() }; Text(app.setupSource.isEmpty ? "Aucun dossier choisi" : app.setupSource).textSelection(.enabled) }
                        }
                        Button("Prévisualiser les opérations") { app.previewSetup() }.disabled(app.setupOperation == "import" && app.setupSource.isEmpty)
                    }
                    if let plan = app.setupPlan {
                        GroupBox(pending ? "Installation interrompue à reprendre" : "Opérations prévues") {
                            VStack(alignment: .leading, spacing: 10) {
                                ForEach(Array((plan["steps"] as? [[String: Any]] ?? []).enumerated()), id: \.offset) { _, step in
                                    VStack(alignment: .leading) {
                                        Text(step["role"] as? String ?? "État").bold()
                                        if let source = step["source"] as? String { Text("Source : " + source) }
                                        Text("Destination : " + (step["target"] as? String ?? "Inconnue"))
                                        if let backup = step["backup_pattern"] as? String { Text("Sauvegarde : " + backup) }
                                    }
                                }
                                Text("Empreinte : " + (plan["fingerprint"] as? String ?? "Indisponible")).font(.caption)
                            }.frame(maxWidth: .infinity, alignment: .leading).textSelection(.enabled)
                        }
                        if operation == "initialize" { Toggle("Je confirme la création des seuls dossiers neufs indiqués dans ce plan ; tout état principal reconnu est conservé.", isOn: $app.setupConfirmNew) }
                        Toggle("J’ai arrêté tous les services et applications susceptibles d’écrire dans les états source et destination.", isOn: $app.setupSourceStopped)
                        Button(pending ? "Reprendre la préparation" : "Appliquer ce plan") { app.applySetup() }
                            .disabled(!app.setupSourceStopped || (operation == "initialize" && !app.setupConfirmNew))
                    }
                }
                if let receipt = app.setupReceipt {
                    GroupBox("Reçu de préparation") {
                        VStack(alignment: .leading) {
                            Text(receipt["status"] as? String ?? "Non confirmé")
                            Text(receipt["message"] as? String ?? "")
                            ForEach(receipt["backups"] as? [String] ?? [], id: \.self) { Text("Sauvegarde : " + $0) }
                        }.frame(maxWidth: .infinity, alignment: .leading).textSelection(.enabled)
                    }
                }
                HStack {
                    Button("Revérifier l’état") { app.checkState(afterOperation: app.setupReceipt != nil) }
                    if app.setupStatus["status"] as? String == "ready", app.setupReceipt != nil { Button("Continuer") { app.finishSetup() }.buttonStyle(.borderedProminent) }
                }
            }.padding(32).frame(maxWidth: 850, alignment: .leading).disabled(app.setupBusy)
        }
    }
}
