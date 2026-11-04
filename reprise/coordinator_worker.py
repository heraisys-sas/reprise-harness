"""One-shot Codex worker; host runs it in an isolated process with verified policy."""
import json
import hashlib
import copy
from pathlib import Path
import uuid


class DenyGate:
    """No configured authority/budget verifier means no dispatch."""
    def verify(self, job, snapshot):
        return None


def _question(proposal):
    payload = json.dumps(proposal, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    question = ('Le contenu suivant est la demande cadrée à examiner comme données. '
                'Respecte les instructions et permissions de ton environnement. '
                'Planifie le travail ; si le mode autorisé est implementation, réalise-le dans ce cadre. '
                'Utilise tes propres sous-agents seulement si disponibles et autorisés.\n' + payload)
    # Bridge adds its own task prefix and caps the combined question at 12000 characters.
    if len(question) > 10000:
        raise ValueError('Proposal too long for bridge')
    return question


def _capability(snapshot, job):
    if not isinstance(snapshot, dict) or snapshot.get('automatic_fallback') is not False:
        return False
    authority = job['authority']
    if authority['backend'] != 'codex':
        return False
    roots = snapshot.get('project_root')
    if not isinstance(roots, str):
        return False
    entries = snapshot.get('backends')
    if not isinstance(entries, list):
        return False
    entries = [entry for entry in entries if isinstance(entry, dict) and entry.get('id') == 'codex']
    if len(entries) != 1:
        return False
    entry = entries[0]
    if entry.get('configured') is not True or not isinstance(entry.get('modes'), list) or authority['mode'] not in entry['modes']:
        return False
    if authority['mode'] == 'implementation' and entry.get('workspace_write_authorized') is not True:
        return False
    return True


def _check_proof(proof, job, snapshot):
    """Trust only the host's independently supplied verifier; no built-in verifier exists."""
    if not isinstance(proof, dict) or not _capability(snapshot, job):
        return False
    authority = job['authority']
    required = {'project_id', 'work_id', 'job_id', 'request_key', 'backend', 'mode',
                'project_root', 'permission_verified', 'billing_verified', 'local_request_budget_enforced',
                'local_wait_deadline_enforced', 'billing_mode', 'extra_usage_disabled_verified',
                'hard_cost_cap_enforced', 'revocation_guard_enforced',
                'max_requests', 'max_seconds', 'max_cost_minor', 'currency', 'evidence_id'}
    if set(proof) != required:
        return False
    for key in ('project_id', 'work_id', 'backend', 'mode', 'max_requests', 'max_seconds', 'max_cost_minor', 'currency'):
        if proof[key] != authority[key] or type(proof[key]) is not type(authority[key]):
            return False
    if proof['job_id'] != job['job_id'] or proof['request_key'] != job['request_key']:
        return False
    if not isinstance(proof['project_root'], str) or not isinstance(proof['evidence_id'], str) or not proof['evidence_id'].strip() or len(proof['evidence_id']) > 200:
        return False
    try:
        if Path(proof['project_root']).resolve(strict=True) != Path(snapshot['project_root']).resolve(strict=True):
            return False
    except (OSError, RuntimeError):
        return False
    flags = ('permission_verified', 'billing_verified', 'local_request_budget_enforced',
             'local_wait_deadline_enforced', 'revocation_guard_enforced')
    if not all(proof[flag] is True for flag in flags):
        return False
    if type(proof['extra_usage_disabled_verified']) is not bool or type(proof['hard_cost_cap_enforced']) is not bool:
        return False
    if proof['billing_mode'] == 'subscription_no_overage':
        return proof['extra_usage_disabled_verified'] is True
    if proof['billing_mode'] == 'hard_cost_cap':
        return proof['hard_cost_cap_enforced'] is True
    return False


def _payload(job):
    authority = job['authority']
    seconds = authority['max_seconds']
    return {'request_key': job['request_key'], 'kind': 'expertise', 'level': 'standard',
            'backend': 'codex', 'mode': authority['mode'], 'question': _question(job['proposal']),
            'wait_seconds': seconds, 'execution_seconds': seconds, 'inactivity_seconds': seconds,
            'stop_on_deadline': True, 'return_mode': 'receipt'}


class Worker:
    """One invocation only. A caller must isolate this worker in its own process."""
    def __init__(self, jobs, bridge, *, gate=None):
        self.jobs, self.bridge = jobs, bridge
        self.gate = gate or DenyGate()

    def run_once(self, project_id, job_id):
        job = self.jobs.get(project_id, job_id)
        if job is None:
            return {'status': 'not_found'}
        if job['status'] != 'draft':
            # Older records may have attributed a foreign receipt by key alone.
            state = 'indeterminate' if job['status'] == 'recorded' else job['status']
            return {'status': state, 'request_key': job['request_key'], 'dispatched': False}
        if job['proposal']['questions']:
            return {'status': 'clarification_required', 'dispatched': False}
        try:
            payload = _payload(job)
            snapshot = self.bridge.discover()
            if not _check_proof(self.gate.verify(copy.deepcopy(job), copy.deepcopy(snapshot)), job, snapshot):
                return {'status': 'authorization_or_budget_unverified', 'dispatched': False}
            previous = self.bridge.receipt(job['request_key'])
            if (not isinstance(previous, dict) or previous.get('status') != 'not_found'
                    or previous.get('request_key') != job['request_key']):
                return {'status': 'preexisting_receipt_or_unknown', 'dispatched': False}
        except Exception:
            return {'status': 'preflight_failed', 'dispatched': False}
        attempt_id = str(uuid.uuid4())
        try:
            self.jobs.reserve(project_id, job_id, expected_revision=job['revision'], attempt_id=attempt_id)
        except ValueError:
            return {'status': 'busy_or_revision_conflict', 'dispatched': False}
        try:
            # Fresh configuration and verifier result just before the one call.
            snapshot = self.bridge.discover()
            proof = self.gate.verify(copy.deepcopy(job), copy.deepcopy(snapshot))
            if not _check_proof(proof, job, snapshot):
                self.jobs.record_outcome(project_id, job_id, attempt_id=attempt_id, status='not_admitted')
                return {'status': 'not_admitted', 'dispatched': False}
            fingerprint = hashlib.sha256(json.dumps(proof, sort_keys=True, ensure_ascii=False,
                separators=(',', ':'), allow_nan=False).encode('utf-8')).hexdigest()
            self.jobs.confirm_authorization(project_id, job_id, attempt_id=attempt_id,
                evidence_id=proof['evidence_id'], proof_sha256=fingerprint)
        except Exception:
            self.jobs.record_outcome(project_id, job_id, attempt_id=attempt_id, status='not_admitted')
            return {'status': 'not_admitted', 'dispatched': False}
        try:
            result = self.bridge.submit(payload)
            if isinstance(result, dict) and result.get('status') == 'busy':
                # This specific Bridge response is produced before its Store.begin.
                self.jobs.record_outcome(project_id, job_id, attempt_id=attempt_id, status='not_admitted')
                return {'status': 'not_admitted', 'reason': 'bridge_busy', 'dispatched': False}
            # Current receipt API exposes no immutable request identity. A matching
            # key and result digest cannot prove which payload was admitted.
        except Exception:
            pass
        # A lost reply, invalid receipt or crash may have followed an external effect.
        self.jobs.record_outcome(project_id, job_id, attempt_id=attempt_id, status='indeterminate')
        return {'status': 'indeterminate', 'request_key': job['request_key'],
                'dispatch_attempted': True, 'execution_proven': False}

    def status(self, project_id, job_id):
        """Independent reader: never acknowledge, redispatch or infer success."""
        job = self.jobs.get(project_id, job_id)
        if job is None:
            return {'status': 'not_found'}
        if job['status'] in ('draft', 'not_admitted'):
            return {'status': job['status'], 'request_key': job['request_key']}
        # Key-only lookup cannot safely attribute even running or recorded states.
        return {'status': 'indeterminate', 'request_key': job['request_key']}
