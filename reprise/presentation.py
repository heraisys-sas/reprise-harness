"""Summarize recorded attempts without treating them as current service failures."""
import time

PENDING = {'ready', 'waiting_quota', 'running', 'launching'}
CAUSES = {
    'blocked_trust': ('Dossier refusé par le CLI', 'Ouvrir le bon dossier du projet et vérifier sa confiance dans le CLI concerné.'),
    'blocked_owner': ('Session déjà ouverte dans un autre moteur', 'Revenir à la session ouverte dans son application. La reprise doit passer par son moteur actuel.'),
    'blocked_parent': ('Sous-agent à reprendre depuis son parent', 'Continuer la session parente pour qu’elle reprenne ses sous-agents.'),
    'blocked_auth': ('Connexion au fournisseur requise', 'Se reconnecter dans l’application concernée avant toute nouvelle tentative explicite.'),
    'quota_blocked': ('Quota encore indisponible', 'Attendre une disponibilité confirmée ; aucun fournisseur payant de secours.'),
    'blocked_permission': ('Approbation nécessaire', 'Examiner la demande dans la session concernée.'),
    'needs_input': ('Réponse attendue', 'Répondre dans la session concernée.'),
    'needs_path': ('Dossier indisponible', 'Reconnecter ou vérifier le dossier du projet.'),
    'queued_parent': ('Demande déposée auprès du parent', 'Vérifier son action ; la reprise de l’enfant n’est pas encore confirmée.'),
    'delegated_parent': ('Parent sollicité', 'Vérifier que le parent a effectivement repris l’enfant.'),
}


def summarize(tasks, now=None):
    now = time.time() if now is None else now
    groups, pending, history = {}, [], 0
    for task in tasks:
        state = task['status']
        if state in PENDING:
            pending.append(task)
            continue
        history += 1
        if state in ('paused', 'finished', 'cancelled'):
            continue
        title, action = CAUSES.get(state, ('Résultat non confirmé', 'Examiner les détails avant de décider d’une nouvelle tentative.'))
        group = groups.setdefault(state, {'id': state, 'title': title, 'action': action, 'count': 0, 'recent': 0, 'last_at': None, 'task_ids': []})
        group['count'] += 1
        group['task_ids'].append(task['id'])
        stamp = task.get('ended_at') or task.get('started_at') or task.get('due')
        if isinstance(stamp, (int, float)):
            group['last_at'] = max(group['last_at'] or stamp, stamp)
            group['recent'] += int(0 <= now-stamp <= 86400)
    ordered = sorted(groups.values(), key=lambda g: g['last_at'] or 0, reverse=True)
    pending.sort(key=lambda t: (t['status'] not in ('running', 'launching'), t.get('due', 0)))
    return {'pending': pending, 'history_count': history, 'review_count': sum(g['count'] for g in ordered),
            'cause_count': len(ordered), 'groups': ordered}
