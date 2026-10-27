// ClaudeApply — prototype d'envoi `apply` via WKWebView, sans CDP ni Accessibilité.
//
// Usage :
//   ClaudeApply --check [--chat <uuid>] [--timeout 20]
//   ClaudeApply --apply <base64(texte)> [--chat <uuid>] [--timeout 30]
//
// Sortie : un objet JSON sur stdout, même contrat que desktop_apply.py :
//   {ok, stage, hasComposer, url, title, sendDisabled, detail}
//
// Le processus charge claude.ai dans une fenêtre hors écran, attend le
// composeur, écrit le texte puis clique « Envoyer ». Aucune frappe système.

import AppKit
import WebKit

func json(_ payload: [String: Any]) -> String {
    let data = try! JSONSerialization.data(withJSONObject: payload, options: [.sortedKeys])
    return String(data: data, encoding: .utf8) ?? "{}"
}

func emit(_ payload: [String: Any]) -> Never {
    print(json(payload))
    exit(payload["ok"] as? Bool == true ? 0 : 1)
}

func jsString(_ value: String) -> String {
    let data = try! JSONSerialization.data(withJSONObject: [value], options: [])
    let text = String(data: data, encoding: .utf8) ?? "[\"\"]"
    return String(text.dropFirst().dropLast())
}

func arg(_ name: String) -> String? {
    let args = CommandLine.arguments
    guard let index = args.firstIndex(of: "--\(name)"), index + 1 < args.count else { return nil }
    return args[index + 1]
}

func flag(_ name: String) -> Bool { CommandLine.arguments.contains("--\(name)") }

let timeout = Double(arg("timeout") ?? "20") ?? 20

final class Runner: NSObject, WKNavigationDelegate {
    let webView: WKWebView
    let window: NSWindow
    var loaded = false

    override init() {
        let config = WKWebViewConfiguration()
        config.websiteDataStore = .default()
        webView = WKWebView(frame: NSRect(x: 0, y: 0, width: 1200, height: 900),
                            configuration: config)
        let visible = CommandLine.arguments.contains("--login")
        let origin = visible ? NSPoint(x: 220, y: 180) : NSPoint(x: -4000, y: -4000)
        window = NSWindow(contentRect: NSRect(x: origin.x, y: origin.y, width: 1200, height: 900),
                          styleMask: [.borderless], backing: .buffered, defer: false)
        super.init()
        webView.navigationDelegate = self
        window.contentView = webView
        if visible {
            NSApp.activate(ignoringOtherApps: true)
            window.makeKeyAndOrderFront(nil)
        } else {
            window.orderFrontRegardless()
        }
    }

    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
        loaded = true
    }

    /// Bloque les schémas non-http : la page de connexion tente une bascule
    /// `claude://` qui ouvre l'app Desktop et fait reboucler la webview.
    func webView(_ webView: WKWebView,
                 decidePolicyFor navigationAction: WKNavigationAction,
                 decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        let url = navigationAction.request.url
        let scheme = (url?.scheme ?? "").lowercased()
        let line = "nav[\(scheme)] \(url?.absoluteString ?? "?")\n"
        FileHandle.standardError.write(line.data(using: .utf8)!)
        if scheme == "https" || scheme == "http" || scheme == "about" {
            decisionHandler(.allow)
        } else {
            decisionHandler(.cancel)
        }
    }

    func webView(_ webView: WKWebView,
                 createWebViewWith configuration: WKWebViewConfiguration,
                 for navigationAction: WKNavigationAction,
                 windowFeatures: WKWindowFeatures) -> WKWebView? {
        FileHandle.standardError.write("popup bloqué\n".data(using: .utf8)!)
        return nil
    }

    /// Pompe la boucle runloop jusqu'à condition ou échéance.
    @discardableResult
    func pump(until condition: @escaping () -> Bool, seconds: Double) -> Bool {
        let deadline = Date().addingTimeInterval(seconds)
        while Date() < deadline {
            if condition() { return true }
            RunLoop.current.run(until: Date().addingTimeInterval(0.05))
        }
        return condition()
    }

    /// Évalue du JS en bloquant, avec échéance.
    func eval(_ script: String, seconds: Double = 10) -> Any? {
        var output: Any?
        var done = false
        webView.evaluateJavaScript(script) { value, _ in
            output = value
            done = true
        }
        _ = pump(until: { done }, seconds: seconds)
        return output
    }

    func load(_ url: String) {
        loaded = false
        webView.load(URLRequest(url: URL(string: url)!))
        _ = pump(until: { self.loaded }, seconds: timeout)
        // Laisse le SPA monter après didFinish.
        _ = pump(until: { false }, seconds: 2.0)
    }
}

// Le composeur n'est reconnu qu'avec le bouton d'envoi : la page de connexion
// contient elle aussi un contenteditable, ce qui produisait un faux positif.
let composerProbe = """
(() => {
  const send = document.querySelector('[data-testid="chat-input-send"]');
  const el = document.querySelector('div[contenteditable="true"], textarea');
  if (!send || !el) return null;
  const r = el.getBoundingClientRect();
  if (r.width < 100 || r.height < 10) return null;
  return {w: Math.round(r.width), h: Math.round(r.height), sendDisabled: send.disabled};
})()
"""

let stateProbe = """
(() => {
  const send = document.querySelector('[data-testid="chat-input-send"]');
  const input = document.querySelector('div[contenteditable="true"], textarea');
  const text = ((input ? (input.innerText ?? input.value) : '') || '');
  return {
    url: location.href,
    title: document.title,
    hasText: Boolean(text.trim()),
    textLength: text.trim().length,
    sendDisabled: send ? send.disabled : null
  };
})()
"""

if flag("login") {
    // Fenêtre visible : l'humain se connecte une fois, les cookies persistent
    // dans le bac du bundle (com.heraisys.reprise.claudeapply).
    let app = NSApplication.shared
    app.setActivationPolicy(.regular)
    let runner = Runner()
    runner.load("https://claude.ai/login")
    // Attention : un null JS arrive en NSNull, pas en nil. On teste le type.
    var connected = false
    let deadline = Date().addingTimeInterval(600)
    while Date() < deadline {
        if (runner.eval(composerProbe) as? [String: Any]) != nil { connected = true; break }
        RunLoop.current.run(until: Date().addingTimeInterval(1.0))
    }
    let info = (runner.eval(stateProbe) as? [String: Any]) ?? [:]
    emit(["ok": connected, "stage": connected ? "connecté" : "délai",
          "url": info["url"] ?? "", "title": info["title"] ?? ""])
}

let app = NSApplication.shared
app.setActivationPolicy(.accessory)
let runner = Runner()

let chatID = arg("chat")
let target = chatID.map { "https://claude.ai/chat/\($0)" } ?? "https://claude.ai/new"
runner.load(target)

let composer = runner.eval(composerProbe) as? [String: Any]
guard composer != nil else {
    let info = (runner.eval(stateProbe) as? [String: Any]) ?? [:]
    emit(["ok": false, "stage": "composeur", "hasComposer": false,
          "url": info["url"] ?? target, "title": info["title"] ?? "",
          "detail": "aucun composeur visible (login requis, contrôle anti-bot, ou page non chargée)"])
}

if flag("check") {
    var info = (runner.eval(stateProbe) as? [String: Any]) ?? [:]
    info["ok"] = true
    info["stage"] = "prêt"
    info["hasComposer"] = true
    emit(info)
}

guard let encoded = arg("apply"), let data = Data(base64Encoded: encoded),
      let prompt = String(data: data, encoding: .utf8), !prompt.isEmpty else {
    emit(["ok": false, "stage": "vide", "hasComposer": true, "detail": "texte absent ou illisible"])
}

// 1. focus + sélection totale, 2. insertion, 3. contrôle, 4. clic d'envoi.
let insert = """
(() => {
  const text = \(jsString(prompt));
  const el = document.querySelector('div[contenteditable="true"], textarea');
  if (!el) return {ok: false, reason: 'composeur disparu'};
  el.focus();
  if (el.tagName === 'TEXTAREA') {
    el.value = '';
    el.dispatchEvent(new Event('input', {bubbles: true}));
    el.value = text;
    el.dispatchEvent(new Event('input', {bubbles: true}));
  } else {
    const range = document.createRange();
    range.selectNodeContents(el);
    const sel = window.getSelection();
    sel.removeAllRanges();
    sel.addRange(range);
    document.execCommand('insertText', false, text);
  }
  return {ok: true, after: (el.innerText ?? el.value ?? '').slice(0, 80)};
})()
"""

_ = runner.eval(insert, seconds: 10)
_ = runner.pump(until: { false }, seconds: 0.4)

var state = (runner.eval(stateProbe) as? [String: Any]) ?? [:]
if (state["hasText"] as? Bool) != true {
    emit(["ok": false, "stage": "frappe", "hasComposer": true,
          "detail": "texte absent après insertion", "state": state])
}

if (state["sendDisabled"] as? Bool) == true {
    emit(["ok": false, "stage": "quota", "staged": true, "hasComposer": true,
          "detail": "bouton d'envoi désactivé", "state": state])
}

let clicked = runner.eval("""
(() => {
  const b = document.querySelector('[data-testid="chat-input-send"]');
  if (!b || b.disabled) return false;
  b.click();
  return true;
})()
""") as? Bool ?? false

_ = runner.pump(until: { false }, seconds: 1.0)
state = (runner.eval(stateProbe) as? [String: Any]) ?? state

emit(["ok": clicked, "stage": clicked ? "envoyé" : "envoi", "hasComposer": true,
      "length": prompt.count, "state": state])
