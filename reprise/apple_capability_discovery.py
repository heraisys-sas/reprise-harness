"""Explicit, read-only discovery of the Apple pilot's built-in capabilities.

An ``existing_directory`` result proves only that the supplied path resolves to
an existing directory. The host must independently verify project identity and
authorization. Connector ``applicable`` means only that the manifest project
string matches that resolved path; it does not establish operational readiness.
"""
from pathlib import Path

import apple_pilot
import claude_integration


CONNECTOR_ID = 'reprise-harness'


def _unknown_connector(status='inspection_unavailable'):
    return {
        'id': CONNECTOR_ID,
        'kind': 'mcp',
        'observed_name': 'unknown',
        'status': status,
        'applicable': False,
        'configuration_status': 'unknown',
        'managed': 'unknown',
        'client_loaded': 'unknown',
        'capabilities': [],
    }


def discover(project_path, *, inspector=None):
    """Return built-in skills and the project-matched Claude connector status.

    This function never calls a model or an MCP tool. ``inspector`` is injectable
    for tests; the default only reads Reprise's managed Claude configuration.
    The host must supply an existing project directory. Its resolved path is used
    for an exact comparison with the manifest's project string and is never
    included in the returned value.
    """
    skills = [
        {'id': item['id'], 'version': item['version'],
         'description': item['description'], 'source': 'apple_builtin'}
        for item in apple_pilot.catalog()
    ]
    try:
        project = Path(project_path).expanduser().resolve(strict=True)
        if not project.is_dir():
            raise ValueError('project directory required')
    except (OSError, RuntimeError, TypeError, ValueError):
        return {
            'status': 'invalid_project',
            'project_status': 'unavailable',
            'authorization_status': 'host_verification_required',
            'project_identity_status': 'host_verification_required',
            'skills': skills,
            'connectors': [_unknown_connector('project_unavailable')],
            'inference': 'not_requested',
            'side_effects': 'none',
        }

    inspect = inspector or claude_integration.inspect
    try:
        observed = inspect(str(project))
        if not isinstance(observed, dict):
            raise ValueError('invalid inspection result')
    except Exception:
        # Do not return exception text: it may include paths or configuration.
        return {
            'status': 'partial',
            'project_status': 'existing_directory',
            'authorization_status': 'host_verification_required',
            'project_identity_status': 'host_verification_required',
            'skills': skills,
            'connectors': [_unknown_connector()],
            'inference': 'not_requested',
            'side_effects': 'none',
        }

    observed_project = observed.get('project')
    project_known = isinstance(observed_project, str) and bool(observed_project)
    matches = project_known and observed_project == str(project)
    observed_name = observed.get('connector_name')
    if observed_name not in ('reprise', CONNECTOR_ID):
        observed_name = 'unknown'
    configuration_written = observed.get('configuration_written')
    configuration_status = (
        'configured' if configuration_written is True else
        'not_configured' if configuration_written is False else 'unknown'
    )
    managed = observed.get('managed')
    if type(managed) is not bool:
        managed = 'unknown'
    connector = {
        'id': CONNECTOR_ID,
        'kind': 'mcp',
        'observed_name': observed_name,
        'status': 'applicable' if matches else 'project_mismatch' if project_known else 'project_scope_unknown',
        'applicable': matches,
        'applicability_basis': 'manifest_project_scope_match_only',
        'operational_status': 'unknown',
        'configuration_status': configuration_status,
        'managed': managed,
        # inspect() cannot observe whether Claude Desktop loaded this entry.
        'client_loaded': 'unknown',
        # Discovery never calls tools/list or tools/call.
        'capabilities': [],
    }
    return {
        'status': 'finished',
        'project_status': 'existing_directory',
        'authorization_status': 'host_verification_required',
        'project_identity_status': 'host_verification_required',
        'skills': skills,
        'connectors': [connector],
        'inference': 'not_requested',
        'side_effects': 'none',
    }
