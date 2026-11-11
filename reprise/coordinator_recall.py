"""Explicit project-memory copies for one analysis; never a model tool or permission."""
import copy
import re

import coordinator_jobs as jobs
from delegation_store import digest

SCHEMA = 'reprise-memory-selection-v1'
MAX_FACTS = 8
MAX_BYTES = 12 * 1024


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
        if {key: previous[key] for key in identity} != identity:
            raise ValueError('Memory selection identity conflict')
        return row
    snapshot = read_snapshot(memory_factory(), project, work, fact_ids)
    if digest(snapshot) != snapshot_sha256:
        raise ValueError('Memory selection changed; preview again')
    return local_jobs.attach_memory(project, operation, snapshot=snapshot, **identity)
