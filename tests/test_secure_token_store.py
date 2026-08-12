import pytest

import secure_token_store


class _WindowsBackend:
    priority = 5
    values = {}

    def set_password(self, service, account, value):
        self.values[(service, account)] = value

    def get_password(self, service, account):
        return self.values.get((service, account))

    def delete_password(self, service, account):
        del self.values[(service, account)]


_WindowsBackend.__module__ = 'keyring.backends.Windows'
_WindowsBackend.__name__ = 'WinVaultKeyring'


@pytest.fixture
def backend(monkeypatch):
    value = _WindowsBackend()
    value.values = {}
    monkeypatch.setattr(secure_token_store.sys, 'platform', 'win32')
    monkeypatch.setattr(secure_token_store.keyring, 'get_keyring',
                        lambda: value)
    return value


def test_store_round_trips_through_windows_credential_locker(backend):
    secure_token_store.store_public_token('serialized grant')
    assert secure_token_store.load_public_token() == 'serialized grant'


def test_delete_removes_the_locker_entry(backend):
    secure_token_store.store_public_token('serialized grant')
    assert secure_token_store.delete_public_token() is True
    assert secure_token_store.load_public_token() is None


def test_unsupported_platform_fails_without_a_plaintext_fallback(monkeypatch):
    monkeypatch.setattr(secure_token_store.sys, 'platform', 'linux')
    with pytest.raises(secure_token_store.SecureTokenStoreError,
                       match='Windows only'):
        secure_token_store.store_public_token('serialized grant')


def test_non_windows_keyring_backend_is_refused(monkeypatch):
    class _Fallback:
        priority = 1

    monkeypatch.setattr(secure_token_store.sys, 'platform', 'win32')
    monkeypatch.setattr(secure_token_store.keyring, 'get_keyring',
                        lambda: _Fallback())
    with pytest.raises(secure_token_store.SecureTokenStoreError,
                       match='Active backend'):
        secure_token_store.load_public_token()


def test_write_that_cannot_be_read_back_fails_closed(backend, monkeypatch):
    monkeypatch.setattr(backend, 'get_password', lambda service, account: None)
    with pytest.raises(secure_token_store.SecureTokenStoreError,
                       match='did not return'):
        secure_token_store.store_public_token('serialized grant')
