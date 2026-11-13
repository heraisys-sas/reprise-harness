"""Bounded, noninteractive evidence for the privacy UI.

Only proves that the existing vault key can be accessed. It does not verify
encryption of any particular cache, index or external data source.
"""
import vault


def inspect():
    try:
        vault.check('search-index-v1')
    except ValueError as error:
        # Never relay the native helper's error text or Keychain metadata.
        message = str(error)
        if '(key_missing)' in message:
            return {'status': 'unavailable', 'reason': 'key_missing'}
        if '(keychain_unavailable)' in message:
            return {'status': 'unavailable', 'reason': 'keychain_unavailable'}
        if message.startswith('Coffre Reprise indisponible'):
            return {'status': 'unavailable', 'reason': 'key_unavailable'}
        return {'status': 'error', 'reason': 'check_failed'}
    except Exception:
        return {'status': 'error', 'reason': 'check_failed'}
    return {'status': 'verified', 'scope': 'existing_key_access_only'}
