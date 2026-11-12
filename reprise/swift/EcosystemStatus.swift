import Foundation

/// Presentation follows the scope and freshness of the observation, not configuration alone.
enum EcosystemStatus {
    enum Level { case ready, attention, problem }
    struct Value {
        let level: Level
        let label: String
        let detail: String
    }
    static func present(installed: Bool, detected: Bool) -> Bool { installed || detected }
    static func probe(connected: Bool?, checkedAt: Date?, now: Date = Date()) -> Value {
        guard let checkedAt, now.timeIntervalSince(checkedAt) >= 0,
              now.timeIntervalSince(checkedAt) <= 120, let connected else {
            return Value(level: .attention, label: "Non vérifié", detail: "Test de connexion absent ou périmé.")
        }
        return connected
            ? Value(level: .ready, label: "Connexion testée", detail: "Le service a répondu ; les sessions et les résultats ne sont pas vérifiés.")
            : Value(level: .problem, label: "Test échoué", detail: "Consultez le diagnostic de connexion.")
    }
    static func scheduler(_ state: [String: Any], now: Date = Date()) -> Value {
        if let error = state["last_error"] as? String, !error.isEmpty {
            return Value(level: .problem, label: "Erreur signalée", detail: "Le planificateur a signalé une erreur.")
        }
        guard state["auto_enabled"] as? Bool == true else {
            return Value(level: .attention, label: state["auto_enabled"] as? Bool == false ? "En pause" : "Non vérifié", detail: "La surveillance automatique n'est pas confirmée active.")
        }
        guard let tick = state["last_tick"] as? Double, tick.isFinite,
              now.timeIntervalSince1970 >= tick, now.timeIntervalSince1970 - tick <= 120 else {
            return Value(level: .attention, label: "Activé · activité à confirmer", detail: "Aucun tick récent confirmé.")
        }
        return Value(level: .ready, label: "Tick récent", detail: "Surveillance activée ; une reprise n'est pas une preuve de résultat métier.")
    }
    static func cdp(_ route: [String: Any], checkedAt: Date?, now: Date = Date()) -> Value {
        guard let checkedAt, now.timeIntervalSince(checkedAt) >= 0,
              now.timeIntervalSince(checkedAt) <= 120 else {
            return Value(level: .attention, label: "Non vérifié", detail: "État CDP absent ou périmé.")
        }
        let state = route["state"] as? String
        if route["enabled"] as? Bool == true, state == "prête" {
            return Value(level: .ready, label: "Cible prête", detail: "Observation de la route CDP ; aucune soumission vérifiée.")
        }
        if state == "indisponible" {
            return Value(level: .problem, label: "Indisponible", detail: route["reason"] as? String ?? "Route CDP indisponible.")
        }
        return Value(level: .attention, label: state == "désactivée" ? "Désactivé" : "Non vérifié", detail: route["reason"] as? String ?? "Aucune cible CDP confirmée.")
    }
}
