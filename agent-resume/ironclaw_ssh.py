"""Bounded IronClaw SSH API transport; probe is read-only, POST requires opt-in."""
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import stat
import subprocess
from urllib.parse import parse_qs, urlsplit


class ProbeError(Exception):
    """Sanitized error safe for UI/logging."""


@dataclass(frozen=True)
class Endpoint:
    host: str
    user: str
    port: int
    identity_file: str
    token_url_file: str
    http_port: int = 3000
    timeout: int = 15

    def validate(self):
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.-]{0,252}", self.host):
            raise ProbeError("Invalid SSH host")
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_-]{0,63}", self.user):
            raise ProbeError("Invalid SSH user")
        if any(type(p) is not int or not 1 <= p <= 65535 for p in (self.port, self.http_port)):
            raise ProbeError("Invalid port")
        if type(self.timeout) is not int or not 1 <= self.timeout <= 60:
            raise ProbeError("Invalid timeout")
        for value in (self.identity_file, self.token_url_file):
            if not isinstance(value, str) or not Path(value).is_absolute() or "\n" in value:
                raise ProbeError("Absolute file path required")


def _read_token(filename):
    # Reject symlinks and permissive files before reading any secret bytes.
    try:
        fd = os.open(filename, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "r", encoding="utf-8") as source:
            info = os.fstat(source.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
                raise ProbeError("Token URL file must be owner-only regular file")
            raw = source.read(8193)
        if len(raw) > 8192:
            raise ProbeError("Token URL file too large")
        url = urlsplit(raw.strip())
        values = []
        for part in (url.query, url.fragment):
            values.extend(parse_qs(part, keep_blank_values=True).get("token", []))
        if len(values) != 1 or not re.fullmatch(r"[A-Za-z0-9._~+/=-]{1,4096}", values[0]):
            raise ProbeError("Expected one safe token parameter")
        return values[0]
    except ProbeError:
        raise
    except (OSError, ValueError, UnicodeError):
        raise ProbeError("Cannot read token URL file") from None


def _ssh_args(endpoint):
    return ["/usr/bin/ssh", "-F", "/dev/null", "-T", "-o", "BatchMode=yes",
            "-o", "IdentitiesOnly=yes", "-o", "IdentityAgent=none",
            "-o", "StrictHostKeyChecking=yes", "-o", "UpdateHostKeys=no",
            "-o", f"ConnectTimeout={min(endpoint.timeout, 10)}", "-o", "ForwardAgent=no",
            "-o", "ClearAllForwardings=yes", "-i", endpoint.identity_file,
            "-p", str(endpoint.port), f"{endpoint.user}@{endpoint.host}",
            "/usr/bin/curl --disable --config -"]


def _curl_config(endpoint, method, path, token, max_filesize, payload=None):
    # Token stays on stdin (never argv); the payload cap mirrors the request body limit.
    lines = [f'url = "http://127.0.0.1:{endpoint.http_port}{path}"',
             f'request = "{method}"', 'proxy = ""',
             'header = "Accept: application/json"',
             f'header = "Authorization: Bearer {token}"',
             f'max-time = {endpoint.timeout}', 'connect-timeout = 5',
             f'max-filesize = {max_filesize}', 'silent',
             'write-out = "\\n%{http_code}"']
    if payload is not None:
        body = json.dumps(payload, ensure_ascii=True)
        if len(body) > 65536:
            raise ProbeError('Request too large')
        lines.append('header = "Content-Type: application/json"')
        lines.append('data = "' + body.replace('\\', '\\\\').replace('"', '\\"') + '"')
    return "\n".join(lines) + "\n"


def probe(endpoint):
    """Return only schema/capability evidence, never response content or credentials."""
    endpoint.validate()
    token = _read_token(endpoint.token_url_file)
    config = _curl_config(endpoint, "GET", "/api/webchat/v2/session", token, 65536)
    try:
        result = subprocess.run(_ssh_args(endpoint), input=config, text=True,
                                capture_output=True, timeout=endpoint.timeout + 5,
                                check=False)
    except subprocess.TimeoutExpired:
        raise ProbeError("SSH probe timed out") from None
    except OSError:
        raise ProbeError("SSH probe could not start") from None
    if result.returncode:
        raise ProbeError("SSH or HTTP transport failed")
    if len(result.stdout) > 65542:
        raise ProbeError("HTTP response too large")
    body, separator, status = result.stdout.rpartition("\n")
    if not separator or not re.fullmatch(r"[1-5][0-9]{2}", status):
        raise ProbeError("Malformed HTTP response")
    code = int(status)
    evidence = {"http_status": code, "authenticated": False,
                "session_channel_configured": False, "session_keys": []}
    if code != 200:
        return evidence
    try:
        payload = json.loads(body)
    except (ValueError, RecursionError):
        raise ProbeError("Invalid session JSON") from None
    if not isinstance(payload, dict):
        raise ProbeError("Invalid session schema")
    channel = payload.get("session_channel_extension_id")
    if not isinstance(channel, str) or not channel.strip():
        raise ProbeError("Session channel missing; delegation unavailable")
    # Schema keys are allowlisted too: arbitrary server keys could contain secrets.
    allowed = {"session_channel_extension_id", "tenant_id", "user_id", "capabilities",
               "attachment_limits", "deployment", "features", "version"}
    evidence.update(authenticated=True, session_channel_configured=True,
                    session_keys=sorted(payload.keys() & allowed))
    return evidence


def request(endpoint, method, path, payload=None, *, allow_submit=False):
    """Bounded API transport; POST requires explicit caller opt-in, never retries."""
    endpoint.validate()
    base = '/api/webchat/v2'
    identifier = r'[A-Za-z0-9_-]{1,128}'
    # GET /threads lists sessions (ids and metadata) but never message content.
    valid_get = path == base + '/session' or path == base + '/threads' or re.fullmatch(
        base + '/threads/' + identifier + r'/timeline\?limit=100(?:&cursor=[A-Za-z0-9%._~-]{1,2048})?', path)
    valid_post = path == base + '/threads' or re.fullmatch(
        base + '/channels/' + identifier + '/messages', path)
    if method not in ('GET', 'POST') or (method == 'GET' and not valid_get) or (method == 'POST' and not valid_post):
        raise ProbeError('API route not allowed')
    if method == 'POST' and allow_submit is not True:
        raise ProbeError('Submission requires explicit opt-in')
    if method == 'GET' and payload is not None:
        raise ProbeError('GET payload not allowed')
    if method == 'POST':
        expected = {'client_action_id'} if path == base + '/threads' else {'client_action_id', 'thread_id', 'content'}
        if not isinstance(payload, dict) or set(payload) != expected:
            raise ProbeError('Invalid submission fields')
        for field in expected - {'content'}:
            if not isinstance(payload[field], str) or not re.fullmatch(identifier, payload[field]):
                raise ProbeError('Invalid submission identifier')
        if 'content' in payload and (not isinstance(payload['content'], str) or not payload['content'].strip()):
            raise ProbeError('Invalid submission content')
    token = _read_token(endpoint.token_url_file)
    config = _curl_config(endpoint, method, path, token, 1048576, payload)
    try:
        result = subprocess.run(_ssh_args(endpoint), input=config, text=True,
                                capture_output=True, timeout=endpoint.timeout + 5, check=False)
    except (OSError, subprocess.TimeoutExpired):
        raise ProbeError('API transport interrupted; outcome unknown') from None
    if result.returncode or len(result.stdout) > 1048582:
        raise ProbeError('API transport failed; outcome unknown')
    body, _, status = result.stdout.rpartition('\n')
    if not re.fullmatch(r'2[0-9]{2}', status):
        raise ProbeError('API did not acknowledge success')
    try:
        value = json.loads(body)
    except (ValueError, RecursionError):
        raise ProbeError('Invalid API JSON') from None
    if not isinstance(value, dict):
        raise ProbeError('Invalid API object')
    return value
