import SwiftUI

struct ApplePilotView: View {
    @ObservedObject var app: App
    @ObservedObject var pilot: ApplePilotUI
    var retryStartupProjects: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            Text("Pilote Apple expérimental").font(.headline)
            Text("Pilote expérimental. Préparez et clarifiez la demande sans exécution. Après votre consentement explicite, vous pouvez relire le manifeste et autoriser une exécution Apple locale, sans repli.")
                .foregroundStyle(.secondary)
            Toggle("Activer le pilote", isOn: $pilot.enabled)
                .disabled(!pilot.canSelect || pilot.hostRunning)
            if app.pilotProjectsLoadFailed {
                Text("Chargement initial des projets non confirmé.").font(.callout)
                Button("Réessayer le chargement initial") { retryStartupProjects() }.disabled(app.setupBusy)
            }
            HStack {
                Button("Charger les projets") { app.refreshProjects() }
                    .disabled(!pilot.enabled || !pilot.canSelect || app.setupBusy)
                Text(app.projectMessage).font(.caption).foregroundStyle(.secondary)
            }
            Picker("Projet", selection: $pilot.projectID) {
                Text("Choisir un projet").tag("")
                if !pilot.projectID.isEmpty && !app.projects.contains(where: { $0["id"] as? String == pilot.projectID }) {
                    Text("Projet enregistré indisponible : " + pilot.projectID).tag(pilot.projectID)
                }
                ForEach(app.projects, id: \.selfID) { project in
                    if let id = project["id"] as? String {
                        Text(project["name"] as? String ?? id).tag(id)
                    }
                }
            }
            .disabled(!pilot.enabled || !pilot.canSelect)
            .onChange(of: pilot.projectID) { _, _ in pilot.clearCheckpoints() }
            HStack {
                TextField("UUID du travail", text: $pilot.workID)
                    .textFieldStyle(.roundedBorder).disabled(!pilot.enabled || !pilot.canSelect)
                    .onChange(of: pilot.workID) { _, _ in pilot.clearCheckpoints() }
                Button("Créer un identifiant") { pilot.workID = UUID().uuidString.lowercased() }
                    .disabled(!pilot.enabled || !pilot.canSelect)
            }
            Text("Un UUID identifie ce travail dans la mémoire locale ; il ne prouve pas l’existence d’un travail dans un autre service.")
                .font(.caption).foregroundStyle(.secondary)
            HStack {
                TextField("UUID du checkpoint (facultatif)", text: $pilot.checkpointID)
                    .textFieldStyle(.roundedBorder).disabled(!pilot.enabled || !pilot.canSelect)
                Button("Lister les checkpoints") { pilot.listCheckpoints() }
                    .disabled(!pilot.enabled || !pilot.canSelect || pilot.projectID.isEmpty || pilot.workID.isEmpty)
            }
            if !pilot.checkpoints.isEmpty {
                Picker("Checkpoint existant", selection: $pilot.checkpointID) {
                    Text("Aucun").tag("")
                    ForEach(pilot.checkpoints, id: \.selfID) { checkpoint in
                        if let id = checkpoint["id"] as? String {
                            Text("Version \(checkpoint["version"] as? Int ?? 0) · \(id)").tag(id)
                        }
                    }
                }.disabled(!pilot.canSelect)
            }
            Picker("Skill intégrée", selection: $pilot.skill) {
                Text("Examiner le checkpoint").tag("checkpoint-review")
                Text("Résumer le contexte").tag("project-summary")
            }.disabled(!pilot.enabled || !pilot.canSelect)
            HStack {
                Button("Ouvrir la session") { pilot.open() }.disabled(!pilot.canOpen)
                Button("Fermer la session") { pilot.close() }.disabled(pilot.closing || pilot.memoryBusy || pilot.preparationBusy || !pilot.hostRunning)
            }
            if pilot.contextNeedsConfiguration { Text(pilot.contextConfigurationMessage).font(.callout) }
            Text(pilot.status).font(.callout).accessibilityLabel("État du pilote : " + pilot.status)
            Divider()
            preparationPanel
            Divider()
            DisclosureGroup("Mémoire du projet — actions de l’opérateur", isExpanded: $pilot.showMemory) {
                memoryPanel
            }
            Divider()
            Text("Conversation temporaire").font(.headline)
            Text("Les réponses et checkpoints sont des données non vérifiées ; la conversation n’est pas conservée après fermeture.")
                .font(.caption).foregroundStyle(.secondary)
            ForEach(pilot.messages) { message in
                VStack(alignment: .leading, spacing: 4) {
                    Text(message.role).font(.caption.bold())
                    Text(message.text).textSelection(.enabled)
                }.frame(maxWidth: .infinity, alignment: .leading)
            }
            TextField("Votre question", text: $pilot.prompt, axis: .vertical)
                .textFieldStyle(.roundedBorder).lineLimit(2...5).disabled(!pilot.sessionOpen || pilot.closing)
            HStack {
                Button("Envoyer") { pilot.send() }.disabled(!pilot.canSend)
                Button("Annuler le tour") { pilot.cancel() }.disabled(!pilot.sessionOpen || !pilot.busy || pilot.closing || pilot.cancelling)
            }
        }.frame(maxWidth: .infinity, alignment: .leading)
    }

    private var preparationPanel: some View {
        GroupBox("Préparation, consentement et exécution locale") {
            VStack(alignment: .leading, spacing: 8) {
                Text("La préparation et le consentement seuls ne lancent aucune exécution. Résolvez les questions avant de consentir, puis validez le manifeste pour exécuter localement.")
                    .font(.caption).foregroundStyle(.secondary)
                HStack {
                    Button("Nouvelle préparation") { pilot.newPreparation() }
                        .disabled(!pilot.enabled || pilot.projectID.isEmpty || pilot.preparationBusy)
                    Button("Charger cette opération") { pilot.loadPreparation() }
                        .disabled(!pilot.enabled || pilot.preparationOperationID.isEmpty || pilot.preparationBusy)
                    Text(pilot.preparationOperationID.isEmpty ? "Aucune opération" : pilot.preparationOperationID)
                        .font(.caption).textSelection(.enabled)
                }
                TextField("Objectif", text: $pilot.proposalObjective, axis: .vertical)
                    .textFieldStyle(.roundedBorder).lineLimit(2...4)
                    .disabled(pilot.preparationBusy || (!pilot.preparationJob.isEmpty && !pilot.preparationCanEdit))
                TextField("Critères d’acceptation, un par ligne", text: $pilot.proposalAcceptance, axis: .vertical)
                    .textFieldStyle(.roundedBorder).lineLimit(2...4)
                    .disabled(pilot.preparationBusy || (!pilot.preparationJob.isEmpty && !pilot.preparationCanEdit))
                TextField("Contraintes, une par ligne", text: $pilot.proposalConstraints, axis: .vertical)
                    .textFieldStyle(.roundedBorder).lineLimit(2...4)
                    .disabled(pilot.preparationBusy || (!pilot.preparationJob.isEmpty && !pilot.preparationCanEdit))
                TextField("Questions non résolues, une par ligne", text: $pilot.proposalQuestions, axis: .vertical)
                    .textFieldStyle(.roundedBorder).lineLimit(2...5)
                    .disabled(pilot.preparationBusy || (!pilot.preparationJob.isEmpty && !pilot.preparationCanEdit))
                HStack {
                    Button(pilot.preparationJob.isEmpty ? "Enregistrer le brouillon" : "Réviser le brouillon") {
                        if pilot.preparationJob.isEmpty { pilot.savePreparation() } else { pilot.revisePreparation() }
                    }
                    .disabled(!pilot.enabled || pilot.preparationOperationID.isEmpty || pilot.projectID.isEmpty
                              || pilot.preparationBusy || pilot.preparationUncertain
                              || (!pilot.preparationJob.isEmpty && !pilot.preparationCanEdit))
                    if let revision = pilot.preparationJob["revision"] as? Int {
                        Text("Révision \(revision)").font(.caption)
                    }
                    Text("Aucune requête : \(pilot.preparationJob["request_key"] is NSNull || pilot.preparationJob["request_key"] == nil ? "oui" : "non")")
                        .font(.caption)
                }
                if pilot.preparationJob["status"] as? String == "preparation" {
                    Button("Valider le contexte sélectionné") { pilot.loadConsentContext() }
                        .disabled(!pilot.enabled || pilot.projectID.isEmpty || pilot.workID.isEmpty || pilot.preparationBusy || pilot.preparationUncertain)
                    if let root = pilot.consentContext["project_root"] as? String {
                        Text("Projet : \(root) · travail : \(pilot.workID)").font(.caption).textSelection(.enabled)
                        Picker("Cible du consentement", selection: $pilot.consentBackend) {
                            Text("Codex — exécution indisponible").tag("codex")
                            Text("Apple local — sans repli").tag("foundation-models")
                        }
                        Text("Expertise sans outils ni écriture. La cible est explicite ; aucune substitution automatique. Disponibilité non vérifiée.").font(.caption)
                        Button("Enregistrer mon consentement") { pilot.authorizePreparation() }
                            .disabled(!pilot.canConsent)
                    }
                }
                if (pilot.preparationJob["operator_authorization"] as? [String: Any])?["backend"] as? String == "foundation-models" {
                    if pilot.preparationJob["status"] as? String == "consented" {
                        Stepper("Échéance et arrêt : \(pilot.localMaxSeconds) s", value: $pilot.localMaxSeconds, in: 1...120)
                            .disabled(pilot.preparationBusy || pilot.preparationUncertain)
                        Button("Préparer le manifeste local") { pilot.planLocalExecution() }
                            .disabled(pilot.preparationBusy || pilot.preparationUncertain)
                        if let manifest = pilot.localPlan["manifest"] as? [String: Any] {
                            Text("Foundation Models local · une exécution · aucun outil ni repli · arrêt à l’échéance").font(.caption)
                            Text(manifest["helper_path"] as? String ?? "").font(.caption).textSelection(.enabled)
                            Text("SHA-256 : \(manifest["helper_sha256"] as? String ?? "")").font(.caption).textSelection(.enabled)
                            Button("Autoriser et exécuter une fois localement") { pilot.startLocalExecution() }.disabled(!pilot.canStartLocal)
                        }
                    }
                    if let execution = pilot.preparationJob["local_execution"] as? [String: Any] {
                        if execution["stage"] as? String != "recorded" {
                            Button("Demander l’arrêt local") { pilot.stopLocalExecution() }.disabled(pilot.preparationBusy)
                        }
                        if let receipt = execution["receipt"] as? [String: Any] {
                            Text("Reçu : \(receipt["run_id"] as? String ?? "") · \(receipt["status"] as? String ?? "inconnu")").font(.caption).textSelection(.enabled)
                            if let answer = receipt["response"] as? String {
                                Text("Réponse générée").font(.caption.bold())
                                Text(answer).textSelection(.enabled)
                                Text("Cette réponse ne prouve pas la réussite de l’objectif. Elle n’est pas enregistrée comme fait ou checkpoint dans la mémoire partagée.")
                                    .font(.caption).foregroundStyle(.secondary)
                            }
                        }
                    }
                }
                if let questions = pilot.preparationJob["proposal"] as? [String: Any],
                   let items = questions["questions"] as? [String], !items.isEmpty {
                    Text("À clarifier").font(.caption.bold())
                    ForEach(Array(items.enumerated()), id: \.offset) { _, question in
                        Text("• " + question).font(.caption).textSelection(.enabled)
                    }
                }
                Text(pilot.preparationStatus).font(.callout)
            }
        }
    }

    private var memoryPanel: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("Les faits et leurs sources sont déclarés, non vérifiés. Aucune écriture n’est déclenchée par le modèle.")
                .font(.caption).foregroundStyle(.secondary)
            HStack {
                Button("Lier au projet choisi") { pilot.bindMemory() }.disabled(!pilot.memoryReady)
                Text(pilot.memoryProject.isEmpty ? "Non liée" : "Projet : " + pilot.memoryProject)
                    .font(.caption).textSelection(.enabled)
            }
            Text(pilot.memoryStatus).font(.callout)
            if !pilot.memoryOperationState.isEmpty {
                Text(pilot.memoryOperationState).textSelection(.enabled)
                Text("Projet de l’opération : " + pilot.memoryOperationProject).font(.caption).textSelection(.enabled)
            }
            if !pilot.draftEditable && pilot.draftProjectID.isEmpty {
                Text("Choisissez d’abord un projet enregistré pour saisir un brouillon mémoire.")
                    .font(.caption).foregroundStyle(.secondary)
            }
            if pilot.draftEditable && !pilot.checkpointEditable {
                Text("Choisissez un UUID de travail valide pour préparer un checkpoint ou sélectionner ses faits.")
                    .font(.caption).foregroundStyle(.secondary)
            }
            if !pilot.draftProjectID.isEmpty {
                Text(pilot.draftProjectID == "unassigned" ? "Brouillon saisi sans projet : copiez-le puis effacez-le avant de choisir un projet." : "Brouillon lié au projet : " + pilot.draftProjectID)
                    .font(.caption).textSelection(.enabled)
                if !pilot.draftMatchesProject {
                    Text("Ce brouillon ne peut pas être enregistré dans le projet sélectionné. Retournez au projet d’origine ou effacez explicitement le brouillon.")
                        .font(.caption).foregroundStyle(.secondary)
                }
                Button("Effacer le brouillon mémoire") { pilot.discardMemoryDraft() }.disabled(pilot.memoryBusy)
            }
            if !pilot.checkpointDraftWorkID.isEmpty {
                Text(pilot.checkpointDraftWorkID == "unassigned" ? "Brouillon de checkpoint saisi sans travail : copiez-le puis effacez-le avant de choisir un travail." : "Brouillon de checkpoint lié au travail : " + pilot.checkpointDraftWorkID)
                    .font(.caption).textSelection(.enabled)
                if !pilot.checkpointDraftMatchesWork {
                    Text("Ce checkpoint ne peut pas être enregistré dans le travail sélectionné. Retournez au travail d’origine ou effacez explicitement le brouillon.")
                        .font(.caption).foregroundStyle(.secondary)
                }
            }
            GroupBox("Faits") {
                VStack(alignment: .leading, spacing: 8) {
                    HStack {
                        Button("Charger les faits") { pilot.loadFacts() }
                        if let offset = pilot.factsNextOffset { Button("Page suivante") { pilot.loadFacts(offset: offset) } }
                    }.disabled(!pilot.memoryReady || pilot.memoryProject != pilot.projectID)
                    ForEach(pilot.facts, id: \.selfID) { fact in
                        if let id = fact["id"] as? String {
                            HStack {
                                Toggle("Inclure", isOn: Binding(get: { pilot.selectedFactIDs.contains(id) }, set: { value in
                                    if value { pilot.selectedFactIDs.insert(id) } else { pilot.selectedFactIDs.remove(id) }
                                })).disabled(!pilot.checkpointEditable)
                                Button("Fait \(id) · révision \(fact["revision"] as? Int ?? 0)") { pilot.loadFact(id) }
                                    .disabled(!pilot.memoryReady)
                            }
                        }
                    }
                    if !pilot.factDetail.isEmpty {
                        Text(String(describing: pilot.factDetail)).font(.caption).textSelection(.enabled)
                    }
                    TextField("Texte du fait", text: $pilot.factText, axis: .vertical).textFieldStyle(.roundedBorder).disabled(!pilot.draftEditable)
                    Button("Enregistrer ce fait") { pilot.createFact() }
                        .disabled(!pilot.memoryReady || pilot.memoryProject != pilot.projectID || pilot.memoryUncertain || pilot.cleanupUnconfirmed || !pilot.draftMatchesProject || !pilot.draftEditable)
                }
            }
            GroupBox("Provenance déclarée du nouvel élément") {
                VStack {
                    TextField("Type de source", text: $pilot.sourceKind)
                    TextField("Référence", text: $pilot.sourceReference)
                    TextField("Auteur de la déclaration", text: $pilot.sourceProducer)
                    TextField("Date d’observation", text: $pilot.sourceObservedAt)
                    TextField("SHA-256 déclaré (facultatif)", text: $pilot.sourceSHA256)
                }.textFieldStyle(.roundedBorder).disabled(!pilot.draftEditable)
            }
            GroupBox("Checkpoint du travail") {
                VStack(alignment: .leading, spacing: 8) {
                    HStack {
                        Button("Charger les checkpoints") { pilot.loadMemoryCheckpoints() }
                        if let offset = pilot.checkpointsNextOffset { Button("Page suivante") { pilot.loadMemoryCheckpoints(offset: offset) } }
                    }.disabled(!pilot.memoryReady || pilot.memoryProject != pilot.projectID)
                    ForEach(pilot.checkpoints, id: \.selfID) { checkpoint in
                        if let id = checkpoint["id"] as? String {
                            HStack {
                                Button("Version \(checkpoint["version"] as? Int ?? 0) · \(id)") { pilot.loadCheckpoint(id) }
                                    .disabled(!pilot.memoryReady)
                                Button("Choisir pour la session") { pilot.checkpointID = id }
                                    .disabled(!pilot.canSelect)
                            }
                        }
                    }
                    if !pilot.checkpointDetail.isEmpty {
                        Text(String(describing: pilot.checkpointDetail)).font(.caption).textSelection(.enabled)
                    }
                    TextField("Dernière version attendue (0 pour la première)", text: $pilot.checkpointVersion).disabled(!pilot.checkpointEditable)
                    TextField("Décisions, une par ligne", text: $pilot.decisions, axis: .vertical).disabled(!pilot.checkpointEditable)
                    TextField("Contraintes, une par ligne", text: $pilot.constraints, axis: .vertical).disabled(!pilot.checkpointEditable)
                    TextField("Effets incertains, un par ligne", text: $pilot.uncertainEffects, axis: .vertical).disabled(!pilot.checkpointEditable)
                    TextField("Prochaine action", text: $pilot.nextAction, axis: .vertical).disabled(!pilot.checkpointEditable)
                    Button("Créer ce checkpoint") { pilot.createCheckpoint() }
                        .disabled(!pilot.memoryReady || pilot.memoryProject != pilot.projectID || pilot.memoryUncertain || pilot.cleanupUnconfirmed || !pilot.draftMatchesProject || !pilot.checkpointDraftMatchesWork || !pilot.draftEditable)
                }.textFieldStyle(.roundedBorder)
            }
            GroupBox("Vérifier une opération") {
                VStack(alignment: .leading, spacing: 8) {
                    Text("Une opération absente ne prouve pas que son exécution est arrêtée. Les éléments listés sont des candidats non vérifiés.")
                        .font(.caption).foregroundStyle(.secondary)
                    HStack {
                        Button("Lister les intentions") { pilot.loadIntents() }
                        if let offset = pilot.intentsNextOffset { Button("Page suivante") { pilot.loadIntents(offset: offset) } }
                    }.disabled(!pilot.memoryReady || pilot.memoryProject != pilot.projectID)
                    ForEach(pilot.memoryIntents, id: \.operationID) { intent in
                        if let id = intent["operation_id"] as? String {
                            Button("Candidat non vérifié : " + id) { pilot.memoryInspectOperationID = id }
                        }
                    }
                    TextField("UUID de l’opération", text: $pilot.memoryInspectOperationID).textFieldStyle(.roundedBorder)
                    Button("Vérifier le reçu") { pilot.inspectOperation() }
                        .disabled(!pilot.memoryReady || pilot.memoryProject != pilot.projectID)
                }
            }
        }.padding(.top, 8)
    }

}

private extension Dictionary where Key == String, Value == Any {
    var selfID: String { self["id"] as? String ?? "" }
    var operationID: String { self["operation_id"] as? String ?? "" }
}

extension ApplePilotUI {
    static func make(app: App) -> ApplePilotUI {
        let state: URL
        if let override = ProcessInfo.processInfo.environment["AGENT_RESUME_STATE"], !override.isEmpty {
            state = URL(fileURLWithPath: (override as NSString).expandingTildeInPath, isDirectory: true)
        } else if app.usesBundledRuntime {
            state = FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent("Library/Application Support/Reprise/state", isDirectory: true)
        } else {
            state = app.root.appendingPathComponent("state", isDirectory: true)
        }
        let pilot = ApplePilotUI(python: URL(fileURLWithPath: app.python),
                            service: app.root.appendingPathComponent("apple_pilot_service.py"), state: state, preferences: .standard)
        return pilot
    }
}
