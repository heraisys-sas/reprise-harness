"""Census CDP en lecture seule pour une cible du registre cdp_targets.

INVARIANT : le census fonctionne aussi sur une cible enabled:false — c'est
l'étape qui précède l'activation d'une route. Il ne saisit jamais de texte,
ne clique jamais, n'envoie rien : seules des évaluations DOM lectures sont
émises sur la page (aucune méthode Input.*, aucun Runtime.evaluate à effet
de bord). L'unique écriture locale est le rapport JSON de census_report.
"""
import argparse
import json
import time
from datetime import datetime
from pathlib import Path

import cdp_targets
import claude_cdp_pipe

CENSUS_ROOT = Path.home() / '.local/share/claude-cdp/census'

# Candidats alternatifs de composer / bouton d'envoi, sondés en plus des
# sélecteurs du registre pour documenter les écarts de sélecteurs provisoires.
_ALT_COMPOSER = '[contenteditable="true"]'
_ALT_SEND_ATTRS = ['data-testid', 'aria-label', 'type', 'formaction', 'class']
_ALT_SEND_JS = ('[...document.querySelectorAll("button, [role=\'button\'], '
                'input[type=\'submit\']")]')

# Empreintes caractéristiques d'une protection antibot (Cloudflare…).
_CHALLENGE_MARKERS = ('turnstile', 'challenge-platform', 'cf-challenge',
                      'ray-id')
_CHALLENGE_TITLES = ('just a moment', 'attention required')


def _probe_script(config):
    selectors = config['selectors']
    sel_json = json.dumps(selectors, ensure_ascii=False)
    return """(() => {
  const sels = %s;
  const found = {};
  for (const [key, sel] of Object.entries(sels)) {
    found[key] = document.querySelector(sel) !== null;
  }
  const alternatives = {composer: [], send: []};
  document.querySelectorAll('[contenteditable="true"], textarea').forEach(el => {
    alternatives.composer.push({
      tag: el.tagName.toLowerCase(),
      contenteditable: el.getAttribute('contenteditable'),
      testid: el.getAttribute('data-testid'),
      placeholder: el.getAttribute('placeholder') || el.getAttribute('data-placeholder') || null,
      classes: (el.className || '').toString().slice(0, 200),
    });
  });
  %s.forEach(el => {
    const attrs = {};
    for (const name of %s) {
      const value = el.getAttribute(name);
      if (value) attrs[name] = value.slice(0, 120);
    }
    alternatives.send.push({tag: el.tagName.toLowerCase(), attrs});
  });
  const markers = %s;
  const titleLower = document.title.toLowerCase();
  let challengeTitle = false;
  for (const marker of ['just a moment', 'attention required']) {
    if (titleLower.includes(marker)) challengeTitle = true;
  }
  let challengeCount = 0;
  const iframes = [];
  document.querySelectorAll('iframe').forEach(el => {
    const id = el.id || '';
    const cls = (el.className || '').toString();
    iframes.push({id: id.slice(0, 120), class: cls.slice(0, 120)});
    if (iframes.length <= 10) {
      for (const marker of markers) {
        if (id.includes(marker) || cls.includes(marker)) challengeCount += 1;
      }
    }
  });
  document.querySelectorAll('[id], [class]').forEach(el => {
    if (el.tagName.toLowerCase() === 'iframe') return;
    const id = el.id || '';
    const cls = (el.className || '').toString();
    for (const marker of markers) {
      if (id.includes(marker) || cls.includes(marker)) {
        challengeCount += 1;
        break;
      }
    }
  });
  return {
    registry_selectors: found,
    alternatives,
    title: document.title,
    url: location.href,
    challenge_suspected: challengeTitle || challengeCount > 0,
    challenge_elements: challengeCount,
    iframes: iframes.slice(0, 10),
  };
})()""" % (sel_json, _ALT_SEND_JS, json.dumps(_ALT_SEND_ATTRS),
           json.dumps(_CHALLENGE_MARKERS))


def _attach(transport, config, timeout_s):
    """Attache une cible page de l'origin, en la créant si absente."""
    deadline = timeout_s
    origin_prefix = config['origin']
    chat_prefix = config['chat_url_pattern'].split('{chat_id}')[0]
    targets = transport.call('Target.getTargets', {}).get('targetInfos', [])
    selected = None
    for info in targets:
        if info.get('type') == 'page' and str(info.get('url', '')).startswith(origin_prefix):
            selected = info
            break
    if selected is None:
        transport.call('Target.createTarget', {'url': origin_prefix + chat_prefix})
        targets = transport.call('Target.getTargets', {}).get('targetInfos', [])
        for info in targets:
            if info.get('type') == 'page' and str(info.get('url', '')).startswith(origin_prefix):
                selected = info
                break
    if selected is None:
        raise RuntimeError(f"Census {config['origin']} : aucune page cible trouvée")
    resp = transport.call('Target.attachToTarget',
                          {'targetId': selected.get('targetId'), 'flatten': True})
    sid = resp.get('sessionId')
    if not isinstance(sid, str):
        raise RuntimeError('Census : attachement CDP sans sessionId')
    transport._session_id = sid
    return deadline


def census(target_name, timeout_s=30, wait_for_ready_s=5):
    """Collecte en lecture seule l'état DOM de la page cible. Jamais d'envoi."""
    config = cdp_targets.load(target_name)
    if config is None:
        raise RuntimeError(f"Cible inconnue : {target_name!r}")
    profile = claude_cdp_pipe._profile_dir(config['profile'])
    transport, proc = claude_cdp_pipe.launch_chrome(profile=profile, origin=config['origin'])
    try:
        _attach(transport, config, timeout_s)
        # Poll borné : un challenge antibot (ex. Cloudflare) peut se résoudre
        # seul ; on réévalue ~1 s tant que le soupçon persiste, puis on
        # collecte quand même — le rapport devient l'empreinte du blocage.
        data = transport.evaluate(_probe_script(config))
        remaining = max(0, int(wait_for_ready_s))
        while isinstance(data, dict) and data.get('challenge_suspected') \
                and remaining > 0:
            time.sleep(1)
            remaining -= 1
            data = transport.evaluate(_probe_script(config))
    finally:
        claude_cdp_pipe.terminate_chrome(proc)
        transport.close()
    if not isinstance(data, dict):
        raise RuntimeError('Census : résultat DOM illisible')
    blocked = bool(data.get('challenge_suspected'))
    found = data.get('registry_selectors', {})
    if blocked:
        verdict = 'blocked_antibot'
    elif any(found.values()):
        verdict = 'ready'
    else:
        verdict = 'selectors_stale'
    return {'target': target_name, 'enabled': config['enabled'],
            'origin': config['origin'], 'profile': config['profile'],
            'verdict': verdict, 'collected': data}


def census_report(target_name, timeout_s=30, wait_for_ready_s=5):
    """Exécute census() puis écrit un rapport JSON daté ; retourne un résumé."""
    result = census(target_name, timeout_s=timeout_s,
                    wait_for_ready_s=wait_for_ready_s)
    CENSUS_ROOT.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime('%Y-%m-%dT%H-%M-%S')
    path = CENSUS_ROOT / f"{target_name}-{stamp}.json"
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2))
    collected = result['collected']
    found = collected.get('registry_selectors', {})
    lines = [f"Census {target_name} ({result['origin']}) — cible "
             f"{'activée' if result['enabled'] else 'désactivée'} — "
             f"verdict {result['verdict']} :",
             f"  page : {collected.get('title', '')!r} {collected.get('url', '')}"]
    for key in sorted(found):
        lines.append(f"  sélecteur registre {key} : "
                     f"{'trouvé' if found[key] else 'ABSENT'}")
    alts = collected.get('alternatives', {})
    lines.append(f"  composites alternatifs : {len(alts.get('composer', []))} ; "
                 f"boutons candidats : {len(alts.get('send', []))}")
    lines.append(f"  éléments challenge : {collected.get('challenge_elements', 0)} "
                 f"; iframes relevées : {len(collected.get('iframes', []))}")
    if result['verdict'] == 'blocked_antibot':
        lines.append("  sélecteurs absents — challenge antibot probable "
                     "(empreinte dans le rapport JSON)")
    elif result['verdict'] == 'selectors_stale':
        lines.append("  sélecteurs absents — possiblement obsolètes")
    lines.append(f"  rapport : {path}")
    return '\n'.join(lines)


def dry_run(target_name):
    """Valide la config de la cible et affiche le plan de census, sans Chrome."""
    config = cdp_targets.load(target_name)
    if config is None:
        raise RuntimeError(f"Cible inconnue : {target_name!r}")
    profile = claude_cdp_pipe._profile_dir(config['profile'])
    chat_prefix = config['chat_url_pattern'].split('{chat_id}')[0]
    lines = [f"Dry-run census {target_name} : config valide.",
             f"  origin {config['origin']} ; profil Chrome {profile}",
             f"  page ouverte : {config['origin']}{chat_prefix}",
             "  collecte prévue (lecture seule) :",
             f"    sélecteurs registre : {', '.join(sorted(config['selectors']))}",
             f"    alternatives : {_ALT_COMPOSER}, textarea, boutons avec "
             f"{', '.join('@' + a for a in _ALT_SEND_ATTRS)}",
             "    title, url",
             f"  cible enabled: {config['enabled']} — aucun envoi, aucune saisie."]
    return '\n'.join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description='Census CDP lecture seule d\'une cible du registre')
    parser.add_argument('target')
    parser.add_argument('--dry-run', action='store_true',
                        help='valide la config et affiche le plan, sans lancer Chrome')
    args = parser.parse_args(argv)
    if args.dry_run:
        print(dry_run(args.target))
        return 0
    print(census_report(args.target))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
