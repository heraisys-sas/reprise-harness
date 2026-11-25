"""Explicit project-memory copies for one analysis; never a model tool or permission."""
import copy
import re

import coordinator_jobs as jobs
from delegation_store import digest

SCHEMA = 'reprise-memory-selection-v1'
SCHEMA_V2 = 'reprise-memory-selection-v2'
MAX_FACTS = 8
MAX_BYTES = 12 * 1024


def qualified_references(references):
    """Validate and canonically order a bounded, namespace-qualified selection."""
    if not isinstance(references, list) or len(references) > MAX_FACTS:
        raise ValueError('Bounded unique fact selection required')
    keys = []
    for reference in references:
        if not isinstance(reference, dict) or set(reference) != {'scope_id', 'fact_id'}:
            raise ValueError('Qualified memory reference required')
        keys.append((jobs._uuid(reference['scope_id']), jobs._uuid(reference['fact_id'])))
    if len(set(keys)) != len(keys):
        raise ValueError('Duplicate fact selection')
    return [{'scope_id': scope, 'fact_id': fact} for scope, fact in sorted(keys)]


def validate_v2(snapshot):
    """Validate a historical v2 copy without consulting live memory or scopes.

    Association validation here is structural, not authorization. Live scope
    eligibility is checked by read_scoped_snapshot before a new approval.
    """
    if (not isinstance(snapshot, dict)
            or set(snapshot) != {'schema', 'project_id', 'work_id', 'facts'}
            or snapshot['schema'] != SCHEMA_V2):
        raise ValueError('Invalid scoped memory selection')
    project, work = jobs._uuid(snapshot['project_id']), jobs._uuid(snapshot['work_id'])
    facts = snapshot['facts']
    if not isinstance(facts, list) or len(facts) > MAX_FACTS:
        raise ValueError('Bounded memory selection required')
    references, scopes, work_scopes = [], {}, set()
    for fact in facts:
        if not isinstance(fact, dict) or 'scope' not in fact:
            raise ValueError('Scoped memory fact required')
        scope = fact['scope']
        if (not isinstance(scope, dict)
                or set(scope) != {'id', 'kind', 'subject_id', 'project_id', 'work_id'}):
            raise ValueError('Invalid memory scope snapshot')
        namespace = jobs._uuid(scope['id'])
        if scope['subject_id'] is not None or scope['project_id'] != project:
            raise ValueError('Memory scope context mismatch')
        if scope['kind'] == 'project':
            if namespace != project or scope['work_id'] is not None:
                raise ValueError('Memory scope context mismatch')
        elif scope['kind'] == 'work':
            if namespace == project or scope['work_id'] != work:
                raise ValueError('Memory scope context mismatch')
            work_scopes.add(namespace)
        else:
            raise ValueError('Memory scope context mismatch')
        if namespace in scopes and scopes[namespace] != scope:
            raise ValueError('Ambiguous memory scope snapshot')
        scopes[namespace] = scope
        # Reuse the exact historical fact validator without changing its v1
        # contract or converting the snapshot returned to the caller.
        legacy = {key: value for key, value in fact.items() if key != 'scope'}
        validate({'schema': SCHEMA, 'project_id': project, 'work_id': work, 'facts': [legacy]})
        references.append({'scope_id': namespace, 'fact_id': fact['id']})
    if len(work_scopes) > 1:
        raise ValueError('Ambiguous memory scope snapshot')
    if references != qualified_references(references):
        raise ValueError('Noncanonical scoped memory selection')
    if len(jobs._json(snapshot).encode()) > MAX_BYTES:
        raise ValueError('Memory selection too large')
    return copy.deepcopy(snapshot)


def validate_selection(snapshot):
    """Explicit dual reader; never upgrade or normalize a historical snapshot."""
    if isinstance(snapshot, dict) and snapshot.get('schema') == SCHEMA_V2:
        return validate_v2(snapshot)
    return validate(snapshot)


def snapshot_references(snapshot):
    snapshot = validate_v2(snapshot)
    return [{'scope_id': fact['scope']['id'], 'fact_id': fact['id']} for fact in snapshot['facts']]


def read_scoped_snapshot(memory, project, work, references):
    """Internal v2 reader; no migration, scope creation, inference or dispatch."""
    from orchestrator_memory import ScopedMemory
    references = qualified_references(references)
    resolved = memory.recall_scopes(project, work)
    project_scope = {'id': project, 'kind': 'project', 'subject_id': None,
                     'project_id': project, 'work_id': None}
    allowed = {project: project_scope}
    if resolved['work_scope'] is not None:
        scope = resolved['work_scope']
        if scope['id'] == project:
            raise ValueError('Recall scope association conflict')
        allowed[scope['id']] = scope
    facts = []
    for reference in references:
        namespace, ident = reference['scope_id'], reference['fact_id']
        if namespace not in allowed:
            raise ValueError('Selected memory scope unavailable')
        if namespace == project:
            fact = memory.get_fact(project, ident)
            if fact is not None:
                fact = dict(fact, scope=project_scope)
        else:
            scoped = ScopedMemory(memory.state, namespace, crypt=memory.crypt)
            if scoped.bound_scope != allowed[namespace]:
                raise ValueError('Recall scope association conflict')
            fact = scoped.get_fact(namespace, ident)
        if fact is None:
            raise ValueError('Selected memory fact unavailable')
        facts.append(fact)
    return validate_v2({'schema': SCHEMA_V2, 'project_id': project, 'work_id': work, 'facts': facts})


def validate(snapshot):
    if not isinstance(snapshot, dict) or set(snapshot) != {'schema', 'project_id', 'work_id', 'facts'} or snapshot['schema'] != SCHEMA:
        raise ValueError('Invalid memory selection')
    jobs._uuid(snapshot['project_id']); jobs._uuid(snapshot['work_id'])
    facts = snapshot['facts']
    if not isinstance(facts, list) or len(facts) > MAX_FACTS:
        raise ValueError('Bounded memory selection required')
    seen = set()
    for fact in facts:
        if not isinstance(fact, dict) or set(fact) != {'id','revision','text','source','created_at','updated_at','recorded_by','status'}:
            raise ValueError('Invalid memory fact snapshot')
        ident = jobs._uuid(fact['id'])
        if ident in seen or type(fact['revision']) is not int or fact['revision'] < 1:
            raise ValueError('Invalid memory fact identity')
        seen.add(ident)
        jobs._text(fact['text'], MAX_BYTES)
        if fact['status'] != 'asserted' or not isinstance(fact['source'], dict) or not isinstance(fact['recorded_by'], dict):
            raise ValueError('Invalid memory provenance')
    if len(jobs._json(snapshot).encode()) > MAX_BYTES:
        raise ValueError('Memory selection too large')
    return copy.deepcopy(snapshot)


def read_snapshot(memory, project, work, fact_ids):
    jobs._uuid(project); jobs._uuid(work)
    if not isinstance(fact_ids, list) or len(fact_ids) > MAX_FACTS:
        raise ValueError('Bounded unique fact selection required')
    for ident in fact_ids: jobs._uuid(ident)
    if len(set(fact_ids)) != len(fact_ids):
        raise ValueError('Bounded unique fact selection required')
    memory._project(project)  # Validate even an explicitly empty selection.
    facts = []
    for ident in sorted(fact_ids):
        jobs._uuid(ident)
        fact = memory.get_fact(project, ident)
        if fact is None:
            raise ValueError('Selected memory fact unavailable')
        facts.append(fact)
    return validate({'schema': SCHEMA, 'project_id': project, 'work_id': work, 'facts': facts})


def attach(local_jobs, memory_factory, project, operation, *, work, fact_ids, snapshot_sha256,
           selection_id, expected_revision, operator_confirmed):
    """Revalidate before copying, without holding any Jobs lock during memory I/O.

    Each fact is read at its own revision, not a multi-store transaction. Deletion
    after this read cannot revoke an already approved historical copy.
    """
    jobs._uuid(selection_id); jobs._uuid(work)
    if (type(expected_revision) is not int or expected_revision < 0
            or not isinstance(snapshot_sha256, str) or not re.fullmatch(r'[0-9a-f]{64}', snapshot_sha256)
            or not isinstance(fact_ids, list) or len(fact_ids) > MAX_FACTS):
        raise ValueError('Invalid memory approval identity')
    for ident in fact_ids: jobs._uuid(ident)
    if len(set(fact_ids)) != len(fact_ids): raise ValueError('Duplicate fact selection')
    if operator_confirmed is not True:
        raise ValueError('Explicit memory approval required')
    row = local_jobs.recover(project, operation)
    if row is None:
        raise ValueError('Job not found')
    previous = row.get('memory_selection')
    identity = {'selection_id': selection_id, 'snapshot_sha256': snapshot_sha256,
                'expected_revision': expected_revision, 'work_id': work, 'fact_ids': fact_ids}
    if previous and previous['selection_id'] == selection_id:
        if {key: previous.get(key) for key in identity} != identity:
            raise ValueError('Memory selection identity conflict')
        return row
    snapshot = read_snapshot(memory_factory(), project, work, fact_ids)
    if digest(snapshot) != snapshot_sha256:
        raise ValueError('Memory selection changed; preview again')
    return local_jobs.attach_memory(project, operation, snapshot=snapshot, **identity)


def attach_scoped(local_jobs, memory_factory, project, operation, *, work, fact_refs,
                  snapshot_sha256, selection_id, expected_revision, operator_confirmed):
    """Approve a qualified copy; accepted retries never re-read live memory."""
    jobs._uuid(selection_id); jobs._uuid(work)
    if (type(expected_revision) is not int or expected_revision < 0
            or not isinstance(snapshot_sha256, str)
            or not re.fullmatch(r'[0-9a-f]{64}', snapshot_sha256)):
        raise ValueError('Invalid memory approval identity')
    references = qualified_references(fact_refs)
    if references != fact_refs:
        raise ValueError('Noncanonical scoped memory selection')
    if operator_confirmed is not True:
        raise ValueError('Explicit memory approval required')
    row = local_jobs.recover(project, operation)
    if row is None:
        raise ValueError('Job not found')
    identity = {'selection_id': selection_id, 'snapshot_sha256': snapshot_sha256,
                'expected_revision': expected_revision, 'work_id': work, 'fact_refs': references}
    previous = row.get('memory_selection')
    if previous and previous['selection_id'] == selection_id:
        if {key: previous.get(key) for key in identity} != identity:
            raise ValueError('Memory selection identity conflict')
        validate_v2(previous['snapshot'])
        if (digest(previous['snapshot']) != snapshot_sha256
                or snapshot_references(previous['snapshot']) != references):
            raise ValueError('Memory selection identity conflict')
        return row
    snapshot = read_scoped_snapshot(memory_factory(), project, work, references)
    if digest(snapshot) != snapshot_sha256:
        raise ValueError('Memory selection changed; preview again')
    return local_jobs.attach_memory(project, operation, snapshot=snapshot, **identity)
