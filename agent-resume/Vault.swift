import Foundation
import CryptoKit
import Security

struct Request: Decodable {
    let operation: String
    let payload: String
    let purpose: String
    let interactive: Bool?
}
struct VaultError: Error {
    let code: String
    let message: String
    var status: OSStatus? = nil
}
func key(create: Bool, interactive: Bool) throws -> SymmetricKey {
    let query: [String: Any] = [kSecClass as String: kSecClassGenericPassword,
        kSecAttrService as String: "local.agent-resume.vault.v1", kSecAttrAccount as String: "data-key"]
    var found: CFTypeRef?
    var lookup = query
    lookup[kSecReturnData as String] = true
    lookup[kSecMatchLimit as String] = kSecMatchLimitOne
    if !interactive { lookup[kSecUseAuthenticationUI as String] = kSecUseAuthenticationUIFail }
    let status = SecItemCopyMatching(lookup as CFDictionary, &found)
    if status == errSecSuccess {
        guard let data = found as? Data, data.count == 32 else {
            throw VaultError(code: "invalid_key", message: "Clé du coffre invalide.")
        }
        return SymmetricKey(data: data)
    }
    guard status == errSecItemNotFound && create else {
        throw VaultError(code: status == errSecItemNotFound ? "key_missing" : "keychain_unavailable",
                         message: "Autorisez explicitement le coffre Reprise puis réessayez.", status: status)
    }
    let generated = SymmetricKey(size: .bits256)
    var entry = query
    entry[kSecValueData as String] = generated.withUnsafeBytes { Data($0) }
    entry[kSecAttrAccessible as String] = kSecAttrAccessibleWhenUnlockedThisDeviceOnly
    entry[kSecAttrSynchronizable as String] = false
    let added = SecItemAdd(entry as CFDictionary, nil)
    if added == errSecDuplicateItem { return try key(create: false, interactive: interactive) }
    guard added == errSecSuccess else {
        throw VaultError(code: "keychain_unavailable", message: "Autorisez explicitement le coffre Reprise puis réessayez.", status: added)
    }
    return generated
}
func writeJSON(_ value: Any) {
    if let encoded = try? JSONSerialization.data(withJSONObject: value, options: [.fragmentsAllowed]) {
        FileHandle.standardOutput.write(encoded)
    }
}
do {
    // The helper is a dedicated process: disable all legacy Keychain UI before any access.
    let disabled = SecKeychainSetUserInteractionAllowed(false)
    guard disabled == errSecSuccess else {
        throw VaultError(code: "interaction_policy_failed", message: "Impossible de désactiver les demandes du Trousseau.", status: disabled)
    }
    let input = FileHandle.standardInput.readDataToEndOfFile()
    guard input.count <= 64 * 1024 * 1024 else { throw VaultError(code: "invalid_request", message: "Requête trop volumineuse.") }
    let request = try JSONDecoder().decode(Request.self, from: input)
    guard ["search-index-v1", "analysis-cache-v1", "test-v1"].contains(request.purpose),
          let payload = Data(base64Encoded: request.payload),
          ["seal", "open", "check", "authorize"].contains(request.operation),
          request.interactive != true || request.operation == "authorize" else {
        throw VaultError(code: "invalid_request", message: "Requête du coffre invalide.")
    }
    if request.operation == "authorize" {
        guard request.interactive == true, payload.isEmpty else {
            throw VaultError(code: "authorization_required", message: "L’autorisation nécessite une action utilisateur explicite.")
        }
        let enabled = SecKeychainSetUserInteractionAllowed(true)
        guard enabled == errSecSuccess else {
            throw VaultError(code: "interaction_policy_failed", message: "Impossible d’autoriser le Trousseau.", status: enabled)
        }
        _ = try key(create: true, interactive: true)
        writeJSON(["authorized": true])
    } else if request.operation == "check" {
        guard payload.isEmpty else { throw VaultError(code: "invalid_request", message: "Requête du coffre invalide.") }
        _ = try key(create: false, interactive: false)
        writeJSON(["ready": true])
    } else {
        let aad = Data(request.purpose.utf8)
        let result: Data
        if request.operation == "seal" {
            result = try AES.GCM.seal(payload, using: key(create: true, interactive: false), authenticating: aad).combined!
        } else {
            result = try AES.GCM.open(AES.GCM.SealedBox(combined: payload), using: key(create: false, interactive: false), authenticating: aad)
        }
        writeJSON(result.base64EncodedString())
    }
} catch {
    let failure = error as? VaultError ?? VaultError(code: "invalid_data", message: "Requête invalide ou données non authentifiées.")
    var details: [String: Any] = ["code": failure.code, "message": failure.message]
    if let status = failure.status { details["status"] = Int(status) }
    writeJSON(["error": details])
    exit(1)
}
