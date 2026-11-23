// metaai_reprise.m — dylib d'injection pour la copie patchée "Meta AI Reprise.app"
//
// Voie QuickEntry transposée à Meta AI (native SwiftUI, pas d'Electron) :
// insertion dans le composer par API in-process (aucune Accessibilité, aucun CDP).
// Chargée via DYLD_INSERT_LIBRARIES dans la copie ré-signée ad-hoc (sans
// hardened runtime). L'original /Applications/Meta AI.app n'est jamais modifié.
//
// Socket : ~/.metaai-reprise-trigger.sock (0600), requêtes JSON newline :
//   {"cmd":"dump"}                       → arbre des fenêtres/vues dans le log
//   {"cmd":"insert","text":"..."}        → écrit le texte dans le NSTextView du composer
// Réponse JSON sur la même connexion, puis fermeture.

#import <AppKit/AppKit.h>
#import <Foundation/Foundation.h>
#import <libproc.h>
#import <sys/socket.h>
#import <sys/stat.h>
#import <sys/un.h>
#import <pwd.h>

static NSString *LogPath(void) {
    return [NSHomeDirectory() stringByAppendingPathComponent:@"Library/Logs/reprise/metaai-reprise-dylib.log"];
}

static NSString *SockPath(void) {
    return [NSHomeDirectory() stringByAppendingPathComponent:@".reprise/metaai-reprise-trigger.sock"];
}

// Ne démarrer le listener que dans le process principal de l'app
// (les helpers WebKit/XPC héritent de la dylib via Sparkle et tenteraient
// de re-binder la socket). Le binaire principal finit par /MacOS/Meta AI.
static BOOL isMainAppProcess(void) {
    char path[PROC_PIDPATHINFO_MAXSIZE];
    if (proc_pidpath(getpid(), path, sizeof(path)) <= 0) return NO;
    NSString *exe = [NSString stringWithUTF8String:path];
    return [exe hasSuffix:@"/Contents/MacOS/Meta AI"];
}

static void mlog(NSString *fmt, ...) NS_FORMAT_FUNCTION(1, 2);
static void mlog(NSString *fmt, ...) {
    va_list args;
    va_start(args, fmt);
    NSString *line = [[NSString alloc] initWithFormat:fmt arguments:args];
    va_end(args);
    NSString *stamped = [NSString stringWithFormat:@"[%@] %@\n",
        [NSDateFormatter localizedStringFromDate:[NSDate date]
                                       dateStyle:NSDateFormatterShortStyle
                                       timeStyle:NSDateFormatterMediumStyle], line];
    NSFileManager *fm = [NSFileManager defaultManager];
    NSString *dir = [LogPath() stringByDeletingLastPathComponent];
    if (![fm fileExistsAtPath:dir]) [fm createDirectoryAtPath:dir withIntermediateDirectories:YES attributes:nil error:nil];
    FILE *f = fopen(LogPath().fileSystemRepresentation, "a");
    if (f) { fputs(stamped.UTF8String, f); fclose(f); }
}

// Collecte récursive des vues d'un type donné (ASTextView, boutons…)
static void collectViews(NSView *view, Class klass, NSMutableArray *out) {
    if (!view) return;
    if ([view isKindOfClass:klass]) [out addObject:view];
    for (NSView *child in view.subviews) collectViews(child, klass, out);
}

static NSString *expandPath(NSString *p) {
    if (![p hasPrefix:@"~"]) return p;
    return [NSHomeDirectory() stringByAppendingPathComponent:[p substringFromIndex:1]];
}

static void dumpWindows(void) {
    mlog(@"=== DUMP début (%lu fenêtres) ===", (unsigned long)NSApp.windows.count);
    for (NSWindow *win in NSApp.windows) {
        mlog(@"WINDOW %@ | frame=%@ | visible=%d | title=%@",
             NSStringFromClass([win class]), NSStringFromRect(win.frame), win.isVisible, win.title);
        if (win.contentView) {
            NSString *tree = [win.contentView performSelector:NSSelectorFromString(@"_subtreeDescription")];
            mlog(@"TREE: %@", tree);
        }
        NSMutableArray *textViews = [NSMutableArray array];
        collectViews(win.contentView, [NSTextView class], textViews);
        mlog(@"TEXTVIEWS: %lu", (unsigned long)textViews.count);
        for (NSTextView *tv in textViews) {
            mlog(@"  TV %@ | frame=%@ | editable=%d | len=%lu", NSStringFromClass([tv class]),
                 NSStringFromRect(tv.frame), tv.isEditable, (unsigned long)tv.string.length);
        }
    }
    mlog(@"=== DUMP fin ===");
}

static BOOL composerInsert(NSString *text) {
    // Chercher le composer dans TOUTES les fenêtres (la key window peut être
    // une vue secondaire sans NSTextView) ; on priorise la key/main window.
    NSMutableArray *windows = [NSMutableArray array];
    NSWindow *key = NSApp.keyWindow;
    NSWindow *main = NSApp.mainWindow;
    if (key) [windows addObject:key];
    if (main && main != key) [windows addObject:main];
    for (NSWindow *w in NSApp.windows) if (w != key && w != main) [windows addObject:w];
    for (NSWindow *win in windows) {
        if (!win.contentView) continue;
        NSMutableArray *textViews = [NSMutableArray array];
        collectViews(win.contentView, [NSTextView class], textViews);
        for (NSTextView *tv in textViews) {
            if (tv.isEditable && tv.isSelectable) {
                [win makeKeyAndOrderFront:nil];
                [win makeFirstResponder:tv];
                [tv setSelectedRange:NSMakeRange(0, tv.string.length)];
                [tv insertText:text replacementRange:NSMakeRange(0, 0)];
                mlog(@"insert: OK (%lu chars) dans %@ (fenêtre %@)", (unsigned long)text.length,
                     NSStringFromClass([tv class]), NSStringFromClass([win class]));
                return YES;
            }
        }
    }
    mlog(@"insert: aucun NSTextView éditable dans %lu fenêtres", (unsigned long)NSApp.windows.count);
    return NO;
}

static NSUInteger composerLen(void) {
    for (NSWindow *win in NSApp.windows) {
        if (!win.contentView) continue;
        NSMutableArray *textViews = [NSMutableArray array];
        collectViews(win.contentView, [NSTextView class], textViews);
        for (NSTextView *tv in textViews) {
            if (tv.isEditable && tv.isSelectable) return tv.string.length;
        }
    }
    return NSNotFound;
}

static BOOL composerSend(void) {
    NSMutableArray *windows = [NSMutableArray array];
    NSWindow *key = NSApp.keyWindow;
    NSWindow *main = NSApp.mainWindow;
    if (key) [windows addObject:key];
    if (main && main != key) [windows addObject:main];
    for (NSWindow *w in NSApp.windows) if (w != key && w != main) [windows addObject:w];
    for (NSWindow *win in windows) {
        if (!win.contentView) continue;
        NSMutableArray *textViews = [NSMutableArray array];
        collectViews(win.contentView, [NSTextView class], textViews);
        for (NSTextView *tv in textViews) {
            if (tv.isEditable && tv.isSelectable) {
                if (tv.string.length == 0) { mlog(@"send: composer vide"); return NO; }
                [win makeKeyAndOrderFront:nil];
                [win makeFirstResponder:tv];
                [tv doCommandBySelector:@selector(insertNewline:)];
                mlog(@"send: insertNewline envoyé au délégué (len avant=%lu)", (unsigned long)tv.string.length);
                return YES;
            }
        }
    }
    mlog(@"send: aucun composer");
    return NO;
}

static void handlePayload(NSDictionary *payload, NSFileHandle *client) {
    NSString *cmd = payload[@"cmd"] ?: @"";
    mlog(@"cmd reçue: %@", cmd);
    NSString *reply;
    if ([cmd isEqualToString:@"dump"]) {
        dumpWindows();
        reply = @"{\"ok\":true,\"note\":\"dump écrit dans le log\"}";
    } else if ([cmd isEqualToString:@"insert"]) {
        BOOL ok = composerInsert(payload[@"text"] ?: @"");
        reply = ok ? @"{\"ok\":true}" : @"{\"ok\":false,\"note\":\"aucun composer\"}";
    } else if ([cmd isEqualToString:@"send"]) {
        BOOL ok = composerSend();
        reply = ok ? @"{\"ok\":true,\"note\":\"insertNewline passé au délégué\"}"
                   : @"{\"ok\":false,\"note\":\"composer vide ou absent\"}";
    } else if ([cmd isEqualToString:@"len"]) {
        NSUInteger l = composerLen();
        reply = [NSString stringWithFormat:@"{\"ok\":true,\"len\":%lu}", (unsigned long)l];
    } else if ([cmd isEqualToString:@"ping"]) {
        reply = [NSString stringWithFormat:@"{\"ok\":true,\"pid\":%d}", (int)getpid()];
    } else {
        reply = @"{\"ok\":false,\"note\":\"cmd inconnue (dump|insert|ping)\"}";
    }
    [client writeData:[(NSString *)[reply stringByAppendingString:@"\n"] dataUsingEncoding:NSUTF8StringEncoding]];
}

static void startListener(void) {
    dispatch_async(dispatch_get_global_queue(QOS_CLASS_UTILITY, 0), ^{
        NSString *sockPath = SockPath();
        unlink(sockPath.fileSystemRepresentation);
        int fd = socket(AF_UNIX, SOCK_STREAM, 0);
        if (fd < 0) { mlog(@"socket: errno=%d", errno); return; }
        struct sockaddr_un addr;
        memset(&addr, 0, sizeof(addr));
        addr.sun_family = AF_UNIX;
        strncpy(addr.sun_path, sockPath.fileSystemRepresentation, sizeof(addr.sun_path) - 1);
        if (bind(fd, (struct sockaddr *)&addr, sizeof(addr)) != 0) { mlog(@"bind: errno=%d", errno); return; }
        chmod(sockPath.fileSystemRepresentation, 0600);
        listen(fd, 4);
        mlog(@"listener prêt sur %@", sockPath);
        while (true) {
            int cfd = accept(fd, NULL, NULL);
            if (cfd < 0) continue;
            NSFileHandle *client = [[NSFileHandle alloc] initWithFileDescriptor:cfd closeOnDealloc:YES];
            NSMutableData *buf = [NSMutableData data];
            NSData *chunk;
            while ((chunk = [client availableData]).length) {
                [buf appendData:chunk];
                if (memchr(chunk.bytes, '\n', chunk.length)) break;
            }
            NSDictionary *payload = nil;
            NSString *line = [[NSString alloc] initWithData:buf encoding:NSUTF8StringEncoding];
            line = [line stringByTrimmingCharactersInSet:NSCharacterSet.whitespaceAndNewlineCharacterSet];
            if (line.length) {
                NSData *json = [line dataUsingEncoding:NSUTF8StringEncoding];
                payload = [NSJSONSerialization JSONObjectWithData:json options:0 error:nil];
                if (![payload isKindOfClass:[NSDictionary class]]) payload = nil;
            }
            dispatch_sync(dispatch_get_main_queue(), ^{
                if (payload) handlePayload(payload, client);
                else [client writeData:[@"{\"ok\":false,\"note\":\"json invalide\"}\n" dataUsingEncoding:NSUTF8StringEncoding]];
            });
            [client closeFile];
        }
    });
}

__attribute__((constructor))
static void metaai_reprise_init(void) {
    mlog(@"dylib injectée dans pid=%d bundle=%@ main=%d", (int)getpid(),
         [[NSBundle mainBundle] bundleIdentifier] ?: @"(aucun)", isMainAppProcess());
    if (!isMainAppProcess()) return;  // helpers : rien à faire
    void (^fire)(void) = ^{ mlog(@"app prête (pid=%d) — lancement listener", (int)getpid()); startListener(); };
    if ([NSThread isMainThread] && NSApp && NSApp.running) {
        dispatch_async(dispatch_get_main_queue(), fire);
    } else {
        [NSNotificationCenter.defaultCenter addObserverForName:NSApplicationDidFinishLaunchingNotification
                                                        object:nil queue:nil
                                                    usingBlock:^(NSNotification *_) { fire(); }];
    }
}
