// sec_shim.m — shim de la Security.framework pour la copie patchée Meta AI Reprise
//
// L'app demande explicitement le groupe keychain d'équipe Meta
// (V9WTTPBFK9.com.meta.endo) : AMFI refuse ce groupe porté par une identité
// étrangère (SIGKILL à l'exec, entitlement vérifié au lancement). Ce shim,
// chargé à la place de Security.framework (chemin réécrit dans la copie),
// ré-exporte la vraie Security et réécrit kSecAttrAccessGroup vers
// « reprise.metaai » (groupe sans préfixe d'équipe, autorisé par l'entitlement
// de la copie). Les secrets restent dans le trousseau login de l'utilisateur,
// isolés du trousseau de l'originale. Aucune valeur secrète n'est loggée.

#import <Foundation/Foundation.h>
#import <Security/Security.h>
#import <dlfcn.h>

static void shimlog(NSString *fmt, ...) NS_FORMAT_FUNCTION(1, 2);
static void shimlog(NSString *fmt, ...) {
    va_list args;
    va_start(args, fmt);
    NSString *line = [[NSString alloc] initWithFormat:fmt arguments:args];
    va_end(args);
    NSString *stamped = [NSString stringWithFormat:@"[sec-shim %@] %@\n",
        [NSDateFormatter localizedStringFromDate:[NSDate date]
                                       dateStyle:NSDateFormatterShortStyle
                                       timeStyle:NSDateFormatterMediumStyle], line];
    FILE *f = fopen([NSHomeDirectory() stringByAppendingPathComponent:@"Library/Logs/reprise/metaai-reprise-dylib.log"].fileSystemRepresentation, "a");
    if (f) { fputs(stamped.UTF8String, f); fclose(f); }
}

static NSString *kOriginalGroup = @"V9WTTPBFK9.com.meta.endo";
static NSString *kTokenService = @"com.meta.prototypes.endo.tokens";

// --- émulation trousseau pour les tokens de session (class=genp) ---
// macOS 26 exige un entitlement pour l'API SecItem moderne ; l'API legacy
// SecKeychain* (celle du CLI /usr/bin/security) n'en demande pas et crée des
// items ACL par chemin dans le trousseau login. On émule SecItem pour ces
// items uniquement ; tout le reste part vers la vraie Security.framework.

static BOOL isTokenQuery(CFDictionaryRef d) {
    NSDictionary *dict = (__bridge NSDictionary *)d;
    return [dict[(id)kSecClass] isEqualToString:(id)kSecClassGenericPassword]
        && [dict[(id)kSecAttrService] isEqualToString:kTokenService]
        && [dict[(id)kSecAttrAccount] isKindOfClass:[NSString class]];
}

static NSString *cfToNs(id v) {
    if (!v) return nil;
    return [(NSString *)v copy];
}

static OSStatus emuFind(NSString *service, NSString *account, CFDictionaryRef query, CFTypeRef *result) {
    UInt32 len = 0;
    void *data = NULL;
    OSStatus r = SecKeychainFindGenericPassword(NULL, (UInt32)service.length,
        service.fileSystemRepresentation, (UInt32)account.length,
        account.fileSystemRepresentation, &len, &data, NULL);
    if (r != errSecSuccess) return r; // errSecItemNotFound (-25300) passe tel quel
    NSDictionary *q = (__bridge NSDictionary *)query;
    if ([q[(id)kSecReturnData] boolValue]) {
        if (result) *result = (__bridge_retained CFTypeRef)[NSData dataWithBytes:data length:len];
    }
    SecKeychainItemFreeContent(NULL, data);
    return errSecSuccess;
}

static OSStatus emuStore(NSString *service, NSString *account, NSData *value) {
    UInt32 len = 0; void *data = NULL;
    OSStatus r = SecKeychainFindGenericPassword(NULL, (UInt32)service.length,
        service.fileSystemRepresentation, (UInt32)account.length,
        account.fileSystemRepresentation, &len, &data, NULL);
    if (r == errSecSuccess) {
        SecKeychainItemRef item = NULL;
        SecKeychainFindGenericPassword(NULL, (UInt32)service.length,
            service.fileSystemRepresentation, (UInt32)account.length,
            account.fileSystemRepresentation, NULL, NULL, &item);
        if (item) {
            r = SecKeychainItemModifyAttributesAndData(item, NULL, (UInt32)value.length, value.bytes);
            CFRelease(item);
        }
        SecKeychainItemFreeContent(NULL, data);
        return r;
    }
    return SecKeychainAddGenericPassword(NULL, (UInt32)service.length,
        service.fileSystemRepresentation, (UInt32)account.length,
        account.fileSystemRepresentation, (UInt32)value.length, value.bytes, NULL);
}

static OSStatus (*real_SecItemAdd)(CFDictionaryRef, CFTypeRef *);
static OSStatus (*real_SecItemCopyMatching)(CFDictionaryRef, CFTypeRef *);
static OSStatus (*real_SecItemUpdate)(CFDictionaryRef, CFDictionaryRef);
static OSStatus (*real_SecItemDelete)(CFDictionaryRef);

static void loadReals(void) {
    if (real_SecItemAdd) return;
    void *h = dlopen("/System/Library/Frameworks/Security.framework/Security", RTLD_NOW);
    real_SecItemAdd = dlsym(h, "SecItemAdd");
    real_SecItemCopyMatching = dlsym(h, "SecItemCopyMatching");
    real_SecItemUpdate = dlsym(h, "SecItemUpdate");
    real_SecItemDelete = dlsym(h, "SecItemDelete");
    shimlog(@"reals chargées: add=%p copy=%p update=%p del=%p",
            real_SecItemAdd, real_SecItemCopyMatching, real_SecItemUpdate, real_SecItemDelete);
}

static NSMutableDictionary *rewritten(CFDictionaryRef query) {
    NSMutableDictionary *d = [(__bridge NSDictionary *)query mutableCopy];
    id g = d[(id)kSecAttrAccessGroup];
    if ([g isKindOfClass:[NSString class]] && [(NSString *)g isEqualToString:kOriginalGroup]) {
        // Pas de réécriture vers un autre groupe : AMFI refuse tout
        // keychain-access-groups sans provisioning profile. On supprime
        // l'attribut : stockage trousseau login classique (ACL app),
        // sans entitlement, isolé du trousseau de l'originale.
        [d removeObjectForKey:(id)kSecAttrAccessGroup];
        shimlog(@"groupe d'équipe supprimé (stockage ACL par défaut)");
    }
    return d;
}

static void logQuery(CFDictionaryRef query) {
    NSDictionary *d = (__bridge NSDictionary *)query;
    id klass = d[(id)kSecClass];
    NSString *cls = nil;
    if ([klass isKindOfClass:[NSString class]]) cls = klass;
    else if (klass) cls = NSStringFromClass([klass class]);
    shimlog(@"requête: class=%@ service=%@ account=%@ returnData=%d returnRef=%d keys=%@",
            cls ?: @"(nil)", d[(id)kSecAttrService] ?: @"(nil)", d[(id)kSecAttrAccount] ?: @"(nil)",
            [d[(id)kSecReturnData] boolValue] ? 1 : 0,
            [d[(id)kSecReturnRef] boolValue] ? 1 : 0,
            [d allKeys]);
}

OSStatus SecItemAdd(CFDictionaryRef attributes, CFTypeRef *result) {
    loadReals();
    if (isTokenQuery(attributes)) {
        NSDictionary *a = (__bridge NSDictionary *)attributes;
        NSData *v = a[(id)kSecValueData];
        if ([v isKindOfClass:[NSData class]]) {
            OSStatus r = emuStore(cfToNs(a[(id)kSecAttrService]), cfToNs(a[(id)kSecAttrAccount]), v);
            shimlog(@"SecItemAdd[emu] %@ → %d", a[(id)kSecAttrAccount], (int)r);
            return r;
        }
    }
    logQuery(attributes);
    OSStatus r = real_SecItemAdd((__bridge CFDictionaryRef)rewritten(attributes), result);
    shimlog(@"SecItemAdd → %d", (int)r);
    return r;
}

OSStatus SecItemCopyMatching(CFDictionaryRef query, CFTypeRef *result) {
    loadReals();
    if (isTokenQuery(query)) {
        NSDictionary *a = (__bridge NSDictionary *)query;
        OSStatus r = emuFind(cfToNs(a[(id)kSecAttrService]), cfToNs(a[(id)kSecAttrAccount]), query, result);
        shimlog(@"SecItemCopyMatching[emu] %@ → %d", a[(id)kSecAttrAccount], (int)r);
        return r;
    }
    logQuery(query);
    OSStatus r = real_SecItemCopyMatching((__bridge CFDictionaryRef)rewritten(query), result);
    shimlog(@"SecItemCopyMatching → %d", (int)r);
    return r;
}

OSStatus SecItemUpdate(CFDictionaryRef query, CFDictionaryRef attributesToUpdate) {
    loadReals();
    if (isTokenQuery(query)) {
        NSDictionary *a = (__bridge NSDictionary *)query;
        NSDictionary *u = (__bridge NSDictionary *)attributesToUpdate;
        NSData *v = u[(id)kSecValueData];
        if ([v isKindOfClass:[NSData class]]) {
            OSStatus r = emuStore(cfToNs(a[(id)kSecAttrService]), cfToNs(a[(id)kSecAttrAccount]), v);
            shimlog(@"SecItemUpdate[emu] %@ → %d", a[(id)kSecAttrAccount], (int)r);
            return r;
        }
    }
    OSStatus r = real_SecItemUpdate((__bridge CFDictionaryRef)rewritten(query), attributesToUpdate);
    shimlog(@"SecItemUpdate → %d", (int)r);
    return r;
}

OSStatus SecItemDelete(CFDictionaryRef query) {
    loadReals();
    if (isTokenQuery(query)) {
        NSDictionary *a = (__bridge NSDictionary *)query;
        UInt32 len = 0; void *data = NULL; SecKeychainItemRef item = NULL;
        OSStatus r = SecKeychainFindGenericPassword(NULL, (UInt32)[a[(id)kSecAttrService] length],
            [cfToNs(a[(id)kSecAttrService]) fileSystemRepresentation],
            (UInt32)[a[(id)kSecAttrAccount] length],
            [cfToNs(a[(id)kSecAttrAccount]) fileSystemRepresentation], &len, &data, &item);
        if (r == errSecSuccess) {
            r = SecKeychainItemDelete(item);
            if (item) CFRelease(item);
            SecKeychainItemFreeContent(NULL, data);
        }
        shimlog(@"SecItemDelete[emu] %@ → %d", a[(id)kSecAttrAccount], (int)r);
        return r;
    }
    OSStatus r = real_SecItemDelete((__bridge CFDictionaryRef)rewritten(query));
    shimlog(@"SecItemDelete → %d", (int)r);
    return r;
}

__attribute__((constructor))
static void sec_shim_init(void) {
    shimlog(@"shim Security chargé (pid=%d)", (int)getpid());
}
