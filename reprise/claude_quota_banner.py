"""(#135) Bannière quota claude.ai lue en CDP read-only — source complémentaire.

Le journal renderer (#133, claude_quota_log.py) ne contient aucun événement au
moment d'un blocage réel (confirmé le 11/11). Reste la bannière affichée dans
le Chrome dédié (profil ~/.local/share/claude-cdp/chrome) : « En pause …
rétabli à HH:MM », « Réinitialisation : HH:MM » — un HH:MM sans date.

Ce module lance le Chrome dédié en cycle court (terminate propre après
lecture), UNIQUEMENT si aucun Chrome ne détient déjà le profil (SingletonLock
hostname-pid + os.kill(pid,0) via check_profile_busy) ; il lit la bannière en
Runtime.evaluate (garde générique sur le texte, pas un sélecteur unique),
résout le jour par fenêtre 5h (maintenant > HH:MM → le lendemain) et renvoie
deadline_status='unknown' quand l'ambiguïté subsiste : jamais inventer.

Patterns CDP réutilisés de claude_desktop_quick.py / claude_cdp_pipe.py
(Endpoint, fallback Target.createTarget, launch_chrome, terminate_chrome).
"""
from datetime import datetime, timedelta
from pathlib import Path
import re
import time
from zoneinfo import ZoneInfo

HHMM = re.compile(r'\b([01]?\d|2[0-3]):([0-5]\d)\b')
MARKERS = ('en pause', 'rétabli à', 'réinitialisation')
RAW_TEXT_MAX = 200
PAGE_TEXT_MAX = 4000
CREATE_TARGET_POLL_SECONDS = 10.0
ORIGIN = 'https://claude.ai'

# JS read-only : texte visible de la page, borné ; aucune écriture, aucun clic.
_PAGE_TEXT_JS = (
    "(function(){var el=document.body;return el?el.innerText.slice(0,"
    + str(PAGE_TEXT_MAX) + "):''})()"
)


def banner_hhmm(text):
    """(heures, minutes) d'un texte de bannière, ou None.

    Le HH:MM est cherché près d'un motif de pause connu ; un horaire ailleurs
    dans la page (autre widget) ne doit pas être pris pour la bannière.
    """
    if not isinstance(text, str):
        return None
    lowered = text.lower()
    for marker in MARKERS:
        start = 0
        while True:
            index = lowered.find(marker, start)
            if index < 0:
                break
            window = text[index:index + 120]
            match = HHMM.search(window)
            if match:
                return int(match.group(1)), int(match.group(2)), text[index:index + RAW_TEXT_MAX]
            start = index + len(marker)
    return None


def resolve_day(hours, minutes, now, zone):
    """Epoch du reset, jour résolu par fenêtre 5h ; None si ambigu.

    maintenant > HH:MM → le reset est le lendemain ; maintenant < HH:MM →
    aujourd'hui ; égalité → ambigu (unknown, jamais de pari). Une heure locale
    inexistante ou ambiguë (DST) est refusée, comme dans claude_quota.
    """
    if not isinstance(zone, ZoneInfo):
        raise ValueError('Explicit ZoneInfo required')
    current = datetime.fromtimestamp(now, zone)
    banner_time = datetime(2000, 1, 1, hours, minutes).time()
    if current.timetz().replace(tzinfo=None) == banner_time:
        return None
    day = current.date() if banner_time > current.time() else current.date() + timedelta(days=1)
    local = datetime(day.year, day.month, day.day, hours, minutes)
    candidates = set()
    for fold in (0, 1):
        candidate = local.replace(tzinfo=zone, fold=fold).timestamp()
        if datetime.fromtimestamp(candidate, zone).replace(tzinfo=None) == local:
            candidates.add(candidate)
    if len(candidates) != 1:
        return None
    due = candidates.pop()
    if due <= now:
        return None
    return due


def parse_banner(text, now, zone):
    """Sortie structurée de la bannière : {resets_at, deadline_status, source, raw_text}.

    Un texte daté (« 27 oct. à 14:40 ») passe d'abord par parse_pause (#12),
    qui connaît la formulation complète ; sinon HH:MM sans date avec
    résolution de jour. Aucun motif reconnu → deadline_status='unknown'.
    """
    import claude_quota
    result = {'resets_at': None, 'deadline_status': 'unknown',
              'source': 'banner_cdp',
              'raw_text': (text or '')[:RAW_TEXT_MAX]}
    if not isinstance(text, str) or not text:
        return result
    due = claude_quota.parse_pause(text, now, zone)
    if due is not None:
        result.update(resets_at=due, deadline_status='future')
        return result
    hhmm = banner_hhmm(text)
    if hhmm is None:
        return result
    due = resolve_day(hhmm[0], hhmm[1], now, zone)
    if due is not None:
        result.update(resets_at=due, deadline_status='future')
    return result


def _page_urls(transport):
    infos = transport.call('Target.getTargets', {}).get('targetInfos', [])
    return [info for info in infos
            if info.get('type') == 'page' and ORIGIN in str(info.get('url', ''))]


def _page_text(transport, poll_seconds=CREATE_TARGET_POLL_SECONDS):
    """Texte de la première page claude.ai ; fallback Target.createTarget."""
    pages = _page_urls(transport)
    if not pages:
        transport.call('Target.createTarget', {'url': ORIGIN})
        deadline = poll_seconds
        while deadline > 0:
            pages = _page_urls(transport)
            if pages:
                break
            time.sleep(0.5)
            deadline -= 0.5
    if not pages:
        return None
    for info in pages:
        transport.attach_to(info.get('targetId'))
        try:
            text = transport.evaluate(_PAGE_TEXT_JS)
        except Exception:
            continue
        if isinstance(text, str) and text:
            return text
    return None


def read_banner(profile=None, launcher=None, now=None, zone=None,
                terminate=None):
    """Cycle court Chrome : lecture read-only de la bannière puis terminate.

    Aucun lancement si le profil est occupé (Chrome opérateur ou daemon) :
    retour profile_busy sans toucher au détenteur. launcher/terminate sont
    injectables pour les tests ; aucun Chrome réel n'est lancé par les tests.
    """
    import time as _time
    import claude_cdp_pipe as cdp
    if not isinstance(zone, ZoneInfo):
        raise ValueError('Explicit ZoneInfo required')
    now = _time.time() if now is None else now
    profile = cdp._profile_dir(profile)
    lock_path, _ = cdp._hostname_pid_lock(profile)
    busy = cdp.check_profile_busy(lock_path)
    if busy:
        return {'ok': False, 'status': 'profile_busy', 'pid': busy.get('pid'),
                'resets_at': None, 'deadline_status': 'unknown', 'source': 'banner_cdp'}
    if not Path(cdp._chrome_bin()).exists():
        return {'ok': False, 'status': 'chrome_missing', 'resets_at': None,
                'deadline_status': 'unknown', 'source': 'banner_cdp'}
    launcher = launcher or cdp.launch_chrome
    terminate = terminate or cdp.terminate_chrome
    try:
        transport, proc = launcher(profile=profile, origin=ORIGIN)
    except Exception as error:
        return {'ok': False, 'status': 'session', 'reason': str(error),
                'resets_at': None, 'deadline_status': 'unknown', 'source': 'banner_cdp'}
    try:
        text = _page_text(transport)
    except Exception as error:
        text = None
        reason = str(error)
    else:
        reason = None
    finally:
        terminate(proc)
    if text is None:
        return {'ok': False, 'status': 'no_page_text',
                'reason': reason, 'resets_at': None,
                'deadline_status': 'unknown', 'source': 'banner_cdp'}
    result = parse_banner(text, now, zone)
    result['ok'] = True
    return result
