"""Pure, conditional planning prototype; never an execution authorization.

All observations and trusted provenance are assertions supplied by a future trusted
collector, not authenticated here. validated_plain represents an allowlisted Git
profile (hooks, filters, fsmonitor and other executable configuration excluded).
No observation proves atomicity: a future executor needs demonstrated isolation and concurrency control.
"""
from dataclasses import asdict, dataclass
import hashlib
import json
import math
import re


@dataclass(frozen=True)
class Request:
    operation: str
    source_repository: str
    destination_repository: str
    destination_ref: str
    expected_oid: str
    target_oid: str


@dataclass(frozen=True)
class Authorization:
    decision: str  # allowed / denied / unknown
    provenance: str  # trusted / unknown; harness assertions are not trusted
    request_fingerprint: str
    evidence_ref: str
    valid_from: float
    valid_until: float


@dataclass(frozen=True)
class Observation:
    source_repository: str
    destination_repository: str
    destination_ref: str
    expected_oid: str
    target_oid: str
    source_common_dir: str
    destination_common_dir: str
    ancestry: str  # ancestor / diverged / behind / unknown
    configuration: str  # validated_plain / unsupported / unknown
    provenance: str
    evidence_ref: str
    observed_at: float
    valid_until: float
    complete: bool | None = None
    objects_available: bool | None = None
    index_clean: bool | None = None
    worktree_clean: bool | None = None
    untracked_clean: bool | None = None
    ignored_clean: bool | None = None
    no_ongoing_operation: bool | None = None
    previous_attempt: str = 'unknown'  # none / completed / active / uncertain


@dataclass(frozen=True)
class Plan:
    status: str
    reasons: tuple[str, ...]
    evidence_refs: tuple[str, ...]
    fingerprint: str
    execution_enabled: bool = False


def fingerprint(value):
    """Content identifier only, never a credential or signature."""
    payload = asdict(value) if hasattr(value, '__dataclass_fields__') else value
    encoded = json.dumps(payload, sort_keys=True, separators=(',', ':'),
                         ensure_ascii=True, allow_nan=False).encode()
    return hashlib.sha256(encoded).hexdigest()


def evaluate(request: Request, authorization: Authorization,
             observation: Observation, *, now: float) -> Plan:
    """Assess a synthetic snapshot, failing closed on unsupported/unknown facts."""
    blocked, missing = [], []
    request_hash = fingerprint(request)
    digest = fingerprint({'request': asdict(request),
                          'authorization': asdict(authorization),
                          'observation': asdict(observation), 'now': now})
    if request.operation != 'git.fast_forward':
        blocked.append('unsupported_operation')
    if any(not value or value.strip() != value for value in
           (request.source_repository, request.destination_repository,
            request.destination_ref)):
        blocked.append('invalid_identity')
    if request.source_repository == request.destination_repository:
        blocked.append('same_repository_identity')
    ref = request.destination_ref
    if (not ref.startswith('refs/heads/') or ref.endswith(('/', '.')) or
            any(part in ref for part in ('..', '@{', '//')) or
            any(c.isspace() or ord(c) < 32 or ord(c) == 127 or c in '~^:?*[\\' for c in ref) or
            any(part.startswith('.') or part.endswith('.lock') for part in ref.split('/'))):
        blocked.append('unsupported_ref')
    if not all(re.fullmatch(r'(?:[0-9a-f]{40}|[0-9a-f]{64})', oid)
               for oid in (request.expected_oid, request.target_oid)):
        blocked.append('invalid_oid')
    if len(request.expected_oid) != len(request.target_oid):
        blocked.append('oid_format_mismatch')
    if authorization.decision == 'denied':
        blocked.append('authorization_denied')
    elif authorization.decision != 'allowed':
        missing.append('authorization_unknown')
    if authorization.provenance != 'trusted':
        missing.append('authorization_untrusted')
    if authorization.request_fingerprint != request_hash:
        blocked.append('authorization_scope_mismatch')
    if not authorization.evidence_ref:
        missing.append('authorization_evidence_missing')
    if not (all(math.isfinite(t) for t in
                    (now, authorization.valid_from, authorization.valid_until)) and
            authorization.valid_from <= now < authorization.valid_until):
        blocked.append('authorization_outside_validity')
    if observation.provenance != 'trusted' or not observation.evidence_ref:
        missing.append('observation_untrusted')
    if not (all(math.isfinite(t) for t in
                    (now, observation.observed_at, observation.valid_until)) and
            observation.observed_at <= now < observation.valid_until):
        missing.append('observation_outside_validity')
    for field in ('source_repository', 'destination_repository',
                  'destination_ref', 'expected_oid', 'target_oid'):
        if getattr(request, field) != getattr(observation, field):
            blocked.append('observation_' + field + '_mismatch')
    if not observation.source_common_dir or not observation.destination_common_dir:
        missing.append('common_dir_unknown')
    elif observation.source_common_dir == observation.destination_common_dir:
        blocked.append('shared_common_dir')
    for field in ('complete', 'objects_available', 'index_clean', 'worktree_clean',
                  'untracked_clean', 'ignored_clean', 'no_ongoing_operation'):
        value = getattr(observation, field)
        if value is not True:
            (blocked if value is False else missing).append(field + '_not_proven')
    if observation.configuration == 'unsupported':
        blocked.append('configuration_unsupported')
    elif observation.configuration != 'validated_plain':
        missing.append('configuration_unknown')
    if observation.previous_attempt in ('active', 'uncertain'):
        blocked.append('previous_attempt_' + observation.previous_attempt)
    elif observation.previous_attempt not in ('none', 'completed'):
        missing.append('previous_attempt_unknown')
    if observation.ancestry in ('diverged', 'behind'):
        blocked.append('ancestry_' + observation.ancestry)
    elif observation.ancestry != 'ancestor':
        missing.append('ancestry_unknown')
    status = ('blocked' if blocked else 'needs_evidence' if missing else
              'noop' if request.expected_oid == request.target_oid else
              'eligible_for_review')
    refs = tuple(sorted({ref for ref in
                         (authorization.evidence_ref, observation.evidence_ref) if ref}))
    return Plan(status, tuple(sorted(set(blocked + missing))), refs, digest)
