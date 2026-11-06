"""Strict host-only candidate extraction. A model response never grants authority."""
import base64
import contextlib
import copy
import hashlib
import json
import os
import sqlite3
import stat

import coordinator_jobs as jobs
from delegation_store import digest

SCHEMA = 'reprise-preparation-candidate-v1'
MAX_RESPONSE = jobs.MAX_PROPOSAL + 1024


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate candidate field')
        result[key] = value
    return result


def _constant(value):
    raise ValueError('Invalid JSON constant')


def parse(text):
    if not isinstance(text, str) or len(text.encode('utf-8')) > MAX_RESPONSE:
        raise ValueError('Bounded candidate response required')
    try:
        value = json.loads(text, object_pairs_hook=_object,
                           parse_constant=_constant)
    except (ValueError, RecursionError):
        raise ValueError('Invalid candidate JSON') from None
    if not isinstance(value, dict) or set(value) != {'schema', 'proposal'} or value['schema'] != SCHEMA:
        raise ValueError('Invalid candidate schema')
    proposal = jobs._proposal(value['proposal'])
    if len(set(proposal['questions'])) != len(proposal['questions']):
        raise ValueError('Duplicate candidate question')
    return {'schema': SCHEMA, 'proposal': proposal}


def snapshot(row):
    """Bind all source content used by attribution and later atomic import."""
    return digest({key: row.get(key) for key in ('project_id', 'operation_id', 'job_id', 'request_key',
        'revision', 'status', 'proposal', 'operator_authorization', 'local_execution', 'clarifications')})


@contextlib.contextmanager
def _read_receipt(state, root, run, binding, job, key, identity, timeout=2):
    # Never construct Store here: its constructor initializes/migrates tables.
    # Open only an existing SQLite file, and never open/close its inode via a raw
    # fd (that would release other threads' POSIX locks in this process).
    directory = state / 'coordinator-jobs.noindex'
    folder = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    db = None
    try:
        jobs._directory(folder)
        before = os.stat('local-admissions.sqlite', dir_fd=folder, follow_symlinks=False)
        if (not stat.S_ISREG(before.st_mode) or before.st_uid != os.getuid()
                or before.st_nlink != 1 or before.st_mode & 0o077):
            raise ValueError('Private admission registry required')
        db = sqlite3.connect((directory / 'local-admissions.sqlite').as_uri() + '?mode=ro', uri=True, timeout=timeout)
        after = os.stat('local-admissions.sqlite', dir_fd=folder, follow_symlinks=False)
        current_directory = os.stat(directory, follow_symlinks=False)
        if ((before.st_dev, before.st_ino) != (after.st_dev, after.st_ino)
                or (os.fstat(folder).st_dev, os.fstat(folder).st_ino) !=
                   (current_directory.st_dev, current_directory.st_ino)):
            raise ValueError('Admission registry changed')
        db.execute('PRAGMA query_only=ON')
        if db.execute('PRAGMA journal_mode').fetchone()[0] not in ('delete', 'truncate', 'persist'):
            raise ValueError('Rollback admission registry required')
        db.execute('BEGIN')
        row = db.execute("""SELECT a.grant_id,a.job_id,a.identity,g.binding,g.payload_identity,
            r.identity,r.state,r.attempt,r.result,g.max_requests,g.used
            FROM guarded_admissions a JOIN guarded_grants g
            ON a.scope=g.scope AND a.grant_id=g.grant_id
            JOIN delegation_requests r ON r.scope=a.scope AND r.request_key=a.request_key
            WHERE a.scope=? AND a.request_key=?""", (root, key)).fetchone()
        if row is None or row[:6] != (run, job, identity, jobs._json(binding), identity, identity) or row[9:] != (1, 1):
            raise ValueError('Candidate source receipt mismatch')
        yield {'state': row[6], 'attempt_id': row[7], 'result': json.loads(row[8])}
    except sqlite3.OperationalError as exc:
        raise ValueError('Candidate source busy or unavailable') from exc
    finally:
        if db is not None:
            db.close()
        os.close(folder)


def extract(local, project, operation, work, run, response_sha256):
    """Read an attributable terminal receipt; never call recover/run or a model."""
    for value in (project, operation, work, run):
        jobs._uuid(value)
    row = local.jobs.recover(project, operation)
    if row is None:
        raise ValueError('Candidate source not found')
    execution = row.get('local_execution') or {}
    manifest = execution.get('manifest') or {}
    receipt = execution.get('receipt') or {}
    consent = row.get('operator_authorization') or {}
    if (row['status'] != 'local_recorded' or execution.get('stage') != 'recorded'
            or execution.get('run_id') != run or manifest.get('work_id') != work
            or manifest.get('project_id') != project or manifest.get('backend') != 'foundation-models'
            or consent.get('backend') != 'foundation-models'
            or (consent.get('host_context') or {}).get('work_id') != work
            or receipt.get('status') != 'finished' or receipt.get('process_started') is not True
            or receipt.get('process_reaped') is not True or receipt.get('exit_code') != 0
            or receipt.get('run_id') != run or receipt.get('job_id') != row['job_id']
            or receipt.get('request_key') != row['request_key']
            or receipt.get('response_sha256') != response_sha256):
        raise ValueError('Candidate source identity mismatch')
    _, frozen_manifest, binding, payload = local._parts(row)
    payload_identity = digest({'payload': payload, 'parent': None})
    if (digest(frozen_manifest) != execution.get('manifest_sha256')
            or receipt.get('manifest_sha256') != execution['manifest_sha256']
            or receipt.get('payload_identity') != payload_identity):
        raise ValueError('Candidate source identity mismatch')
    with _read_receipt(local.jobs.state, manifest['project_root'], run, binding, row['job_id'], row['request_key'], payload_identity) as correlated:
        correlated = copy.deepcopy(correlated)
    stored = copy.deepcopy(correlated.get('result') or {})
    if (correlated.get('state') != 'recorded' or correlated.get('attempt_id') != receipt.get('admission_attempt_id')
            or stored.get('response_sha256') != response_sha256 or not stored.get('response_ciphertext')):
        raise ValueError('Candidate source receipt mismatch')
    answer = local.jobs.crypt(base64.b64decode(stored.pop('response_ciphertext'), validate=True), jobs.PURPOSE, 'open').decode('utf-8')
    stored.update(response=answer, answer_available=True, admission_attempt_id=correlated['attempt_id'])
    if stored != receipt or hashlib.sha256(answer.encode()).hexdigest() != response_sha256:
        raise ValueError('Candidate source result mismatch')
    candidate = parse(answer)
    origin = origin_for(row)
    return {'candidate': candidate, 'candidate_sha256': digest(candidate), 'origin': origin,
            'evidence': {'binding': binding, 'attribution_sha256': digest(correlated)}}


def origin_for(row):
    execution = row['local_execution']; manifest = execution['manifest']; receipt = execution['receipt']
    return {'schema': SCHEMA, 'project_id': row['project_id'], 'work_id': manifest['work_id'], 'source_operation_id': row['operation_id'],
              'project_root': manifest['project_root'], 'context_sha256': manifest['context_sha256'],
              'source_job_id': row['job_id'], 'source_request_key': row['request_key'], 'source_run_id': execution['run_id'],
              'response_sha256': receipt['response_sha256'], 'manifest_sha256': execution['manifest_sha256'],
              'payload_identity': receipt['payload_identity'], 'admission_attempt_id': receipt['admission_attempt_id'],
              'source_snapshot_sha256': snapshot(row)}


@contextlib.contextmanager
def revalidate(state, extracted):
    """Short commit guard: no Jobs wait, crypto, model or subprocess under SQL lock."""
    origin = extracted['origin']; evidence = extracted['evidence']
    with _read_receipt(state, origin['project_root'], origin['source_run_id'], evidence['binding'],
                           origin['source_job_id'], origin['source_request_key'], origin['payload_identity'], timeout=0.25) as correlated:
        if digest(correlated) != evidence['attribution_sha256']:
            raise ValueError('Candidate source receipt changed')
        yield
