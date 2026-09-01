"""Windows Credential Locker storage for the public Google OAuth grant.

The stranger release is supported on Windows.  ``keyring`` is used only
as the adapter to Windows Credential Locker; accepting whatever backend the
package happens to discover would permit plaintext or otherwise unsuitable
fallbacks.  Every operation therefore verifies the concrete Windows backend
before touching a credential.

Token values are never included in errors or logs.  A write is considered
successful only after the same value can be read back from Credential Locker.
"""

import sys

import keyring
from keyring.errors import PasswordDeleteError


SERVICE_NAME = 'Fantasy League Almanac'
PUBLIC_TOKEN_ACCOUNT = 'google-oauth-public-drive-file-v1'


class SecureTokenStoreError(RuntimeError):
    """The required OS credential backend could not safely be used."""


def _backend():
    if sys.platform != 'win32':
        raise SecureTokenStoreError(
            'The public Google workbook flow supports secure OAuth '
            'storage on Windows only. Windows Credential Locker is required; '
            'this app will not fall back to a plaintext token file. Run the '
            'released ZIP on Windows, or wait for a release that explicitly '
            'supports your operating system credential store.'
        )
    try:
        backend = keyring.get_keyring()
    except Exception as exc:
        raise _unavailable(exc) from exc

    backend_type = type(backend)
    if (backend_type.__module__ != 'keyring.backends.Windows'
            or backend_type.__name__ != 'WinVaultKeyring'
            or getattr(backend, 'priority', 0) <= 0):
        raise SecureTokenStoreError(
            'Windows Credential Locker is not available as the active '
            'credential backend. Fantasy League Almanac refuses to store a '
            'Google token in plaintext. Install the release requirements and '
            'run from a normal Windows user session, then try again. Active '
            f'backend: {backend_type.__module__}.{backend_type.__name__}.'
        )
    return backend


def _unavailable(exc):
    return SecureTokenStoreError(
        'Windows Credential Locker could not be accessed. Fantasy League '
        'Almanac did not write a plaintext Google token. Run from a normal '
        'signed-in Windows user session and verify the Windows Credential '
        'Manager service is available, then try again. '
        f'Backend error: {type(exc).__name__}.'
    )


def load_public_token():
    """Return the serialized public grant, or ``None`` when none is stored."""
    backend = _backend()
    try:
        return backend.get_password(SERVICE_NAME, PUBLIC_TOKEN_ACCOUNT)
    except Exception as exc:
        raise _unavailable(exc) from exc


def store_public_token(serialized):
    """Store and verify the public grant without exposing its value."""
    if not isinstance(serialized, str) or not serialized:
        raise SecureTokenStoreError(
            'Refusing to store an empty Google authorization credential.'
        )
    backend = _backend()
    try:
        backend.set_password(SERVICE_NAME, PUBLIC_TOKEN_ACCOUNT, serialized)
        verified = backend.get_password(SERVICE_NAME, PUBLIC_TOKEN_ACCOUNT)
    except Exception as exc:
        raise _unavailable(exc) from exc
    if verified != serialized:
        raise SecureTokenStoreError(
            'Windows Credential Locker did not return the Google credential '
            'that was just written. The authorization was not cached; no '
            'plaintext fallback was created. Check Credential Manager and '
            'try again.'
        )


def delete_public_token():
    """Delete the locally stored public grant; return whether one existed."""
    backend = _backend()
    try:
        if backend.get_password(SERVICE_NAME, PUBLIC_TOKEN_ACCOUNT) is None:
            return False
        backend.delete_password(SERVICE_NAME, PUBLIC_TOKEN_ACCOUNT)
    except PasswordDeleteError:
        return False
    except Exception as exc:
        raise _unavailable(exc) from exc
    return True
