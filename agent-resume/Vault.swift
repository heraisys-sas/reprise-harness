import Foundation
import CryptoKit
import Security

struct Request: Decodable { let operation: String; let payload: String; let purpose: String }
func key(create: Bool) throws -> SymmetricKey {
    let query: [String: Any] = [kSecClass as String: kSecClassGenericPassword,
        kSecAttrService as String: "local.agent-resume.vault.v1", kSecAttrAccount as String: "data-key"]
    var found: CFTypeRef?
    var lookup = query
    lookup[kSecReturnData as String] = true
    lookup[kSecMatchLimit as String] = kSecMatchLimitOne
    let status = SecItemCopyMatching(lookup as CFDictionary, &found)
    if status == errSecSuccess, let data = found as? Data, data.count == 32 { return SymmetricKey(data: data) }
    guard status == errSecItemNotFound && create else { throw NSError(domain: "RepriseKeychain", code: Int(status)) }
    let generated = SymmetricKey(size: .bits256)
    let data = generated.withUnsafeBytes { Data($0) }
    var entry = query
    entry[kSecValueData as String] = data
    entry[kSecAttrAccessible as String] = kSecAttrAccessibleWhenUnlockedThisDeviceOnly
    entry[kSecAttrSynchronizable as String] = false
    let added = SecItemAdd(entry as CFDictionary, nil)
    if added == errSecDuplicateItem { return try key(create: false) }
    guard added == errSecSuccess else { throw NSError(domain: "RepriseKeychain", code: Int(added)) }
    return generated
}
do {
    let input = FileHandle.standardInput.readDataToEndOfFile()
    guard input.count <= 64 * 1024 * 1024 else { throw NSError(domain: "RepriseSize", code: 1) }
    let request = try JSONDecoder().decode(Request.self, from: input)
    guard ["search-index-v1", "analysis-cache-v1", "test-v1"].contains(request.purpose), let payload = Data(base64Encoded: request.payload) else { throw NSError(domain: "RepriseRequest", code: 1) }
    let result: Data
    let aad = Data(request.purpose.utf8)
    switch request.operation {
    case "seal":
        result = try AES.GCM.seal(payload, using: key(create: true), authenticating: aad).combined!
    case "open":
        result = try AES.GCM.open(AES.GCM.SealedBox(combined: payload), using: key(create: false), authenticating: aad)
    default: throw NSError(domain: "RepriseOperation", code: 1)
    }
    FileHandle.standardOutput.write(try JSONEncoder().encode(result.base64EncodedString()))
} catch {
    FileHandle.standardError.write(Data("Reprise : coffre indisponible ou données non authentifiées.\n".utf8))
    exit(1)
}
