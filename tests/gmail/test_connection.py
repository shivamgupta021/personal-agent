import base64
import hashlib
from urllib.parse import parse_qs, urlencode, urlparse

from datetime import datetime, timedelta, timezone
from io import StringIO
import json
from pathlib import Path
from typing import Any

from django.core.management import call_command
from django.core.management.base import CommandError
from google.oauth2.credentials import Credentials
import pytest
import requests

from gmail.client import GoogleClient, TimeoutAdapter
from gmail.commands import GmailCommand
from gmail.constants import AUTH_URLS, HTTP_TIMEOUT_SECONDS, PROFILE_URL, REVOKE_URL, SCOPES, TOKEN_URL
from gmail.credentials import Connection, KeychainStore
from gmail.errors import GmailError
from gmail.service import GmailService


class MemoryKeychain:
    def __init__(self) -> None:
        self.entries: dict[tuple[str, str], str] = {}
        self.fail_save = False
        self.fail_delete = False

    def get_password(self, service: str, account: str) -> str | None:
        return self.entries.get((service, account))

    def set_password(self, service: str, account: str, value: str) -> None:
        # Match the real backend's delete-before-write behavior.
        self.entries.pop((service, account), None)
        if self.fail_save:
            raise RuntimeError('SECRET write failed')
        self.entries[service, account] = value

    def delete_password(self, service: str, account: str) -> None:
        if self.fail_delete:
            raise RuntimeError('SECRET delete failed')
        del self.entries[service, account]


class ControlledGoogle(GoogleClient):
    def __init__(self) -> None:
        self.email = 'owner@example.com'
        self.credentials = self.token()
        self.refreshes = 0
        self.revoked = False
        self.revoke_failure = False
        self.authorization_error = False
        self.refresh_error = False

    @staticmethod
    def token(*, scopes: tuple[str, ...] = SCOPES) -> Credentials:
        return Credentials('SECRET-access', refresh_token='SECRET-refresh', token_uri=TOKEN_URL,
            client_id='synthetic-client', client_secret='SECRET-client', scopes=list(scopes),
            expiry=datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=1))

    def authorize(self, client_file: Path, expected_email: str, *, scopes: tuple[str, ...] = SCOPES) -> Credentials:
        if self.authorization_error:
            raise RuntimeError('SECRET denied or cancelled')
        return self.credentials

    def profile(self, credentials: Credentials) -> str:
        return self.email

    def refresh(self, credentials: Credentials) -> None:
        self.refreshes += 1
        if self.refresh_error:
            raise RuntimeError('SECRET invalid_grant')
        credentials.token = 'SECRET-refreshed'
        credentials.expiry = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=1)

    def revoke(self, credentials: Credentials) -> bool:
        self.revoked = True
        if self.revoke_failure:
            raise RuntimeError('SECRET provider unavailable')
        return True


@pytest.fixture
def connection(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[GmailService, ControlledGoogle, MemoryKeychain]:
    store = KeychainStore(tmp_path, 'test-owner')
    backend = MemoryKeychain()
    monkeypatch.setattr(store, 'backend', backend)
    client = ControlledGoogle()
    return GmailService(store, client, tmp_path), client, backend


def test_connect_restart_refresh_and_disconnect(connection: tuple[GmailService, ControlledGoogle, MemoryKeychain], tmp_path: Path) -> None:
    service, client, backend = connection
    assert 'owner@example.com' in service.connect(tmp_path / 'oauth.json', client.email)
    # Persisted credentials expire; a new service must refresh and save them.
    saved = service.store.load()
    assert saved is not None
    saved.credentials.expiry = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=1)
    service.store.save(saved)
    restarted = GmailService(service.store, client, tmp_path)
    assert 'verified' in restarted.status()
    assert client.refreshes == 1
    persisted = service.store.load()
    assert persisted is not None and persisted.credentials.token == 'SECRET-refreshed'
    assert persisted.credentials.refresh_token == 'SECRET-refresh'
    for file in tmp_path.rglob('*'):
        if file.is_file():
            assert 'SECRET' not in file.read_text()
    assert 'Google accepted revocation' in restarted.disconnect()
    assert client.revoked and not backend.entries
    assert restarted.status() == 'Gmail is not connected.'


@pytest.mark.parametrize('failure', ['cancel', 'scope', 'offline', 'account', 'storage'])
def test_failed_reconnect_preserves_previous_credentials(connection: tuple[GmailService, ControlledGoogle, MemoryKeychain], tmp_path: Path, failure: str) -> None:
    service, client, backend = connection
    service.connect(tmp_path / 'oauth.json', client.email)
    old = service.store.load()
    assert old is not None
    original = old.credentials.to_json()
    if failure == 'cancel':
        client.authorization_error = True
    elif failure == 'scope':
        client.credentials = client.token(scopes=(*SCOPES, 'https://www.googleapis.com/auth/gmail.compose'))
    elif failure == 'offline':
        client.credentials = Credentials('SECRET-no-offline', scopes=list(SCOPES))
    elif failure == 'account':
        client.email = 'other@example.com'
    else:
        backend.fail_save = True
    with pytest.raises(GmailError) as error:
        service.connect(tmp_path / 'oauth.json', 'owner@example.com')
    assert 'SECRET' not in str(error.value)
    preserved = service.store.load()
    assert preserved is not None and preserved.email == 'owner@example.com'
    assert preserved.credentials.to_json() == original


def test_concurrent_operation_rejected_and_lock_released_after_failure(connection: tuple[GmailService, ControlledGoogle, MemoryKeychain], tmp_path: Path) -> None:
    service, client, _ = connection
    with service.operation():
        with pytest.raises(GmailError, match='Another Gmail'):
            GmailService(service.store, client, tmp_path).status()
    client.authorization_error = True
    with pytest.raises(GmailError):
        service.connect(tmp_path / 'oauth.json', client.email)
    assert service.status() == 'Gmail is not connected.'


def test_failed_refresh_keeps_saved_grant_and_sanitizes_error(connection: tuple[GmailService, ControlledGoogle, MemoryKeychain], tmp_path: Path) -> None:
    service, client, _ = connection
    client.credentials.expiry = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=1)
    service.connect(tmp_path / 'oauth.json', client.email)
    client.refresh_error = True
    with pytest.raises(GmailError) as error:
        service.status()
    assert 'SECRET' not in str(error.value)
    assert service.store.load() is not None


@pytest.mark.parametrize('failure', ['remote', 'storage'])
def test_disconnect_disables_access_even_when_cleanup_fails(connection: tuple[GmailService, ControlledGoogle, MemoryKeychain], tmp_path: Path, failure: str) -> None:
    service, client, backend = connection
    service.connect(tmp_path / 'oauth.json', client.email)
    if failure == 'remote':
        client.revoke_failure = True
        assert 'not confirmed' in service.disconnect()
    else:
        backend.fail_delete = True
        with pytest.raises(GmailError, match='disabled locally'):
            service.disconnect()
        backend.fail_delete = False
        service.disconnect()
    assert service.status() == 'Gmail is not connected.'
    assert not backend.entries


def test_command_boundary_has_no_secrets(connection: tuple[GmailService, ControlledGoogle, MemoryKeychain], tmp_path: Path, monkeypatch: pytest.MonkeyPatch, settings: Any) -> None:
    service, client, _ = connection
    def authorize(client_file: Path, expected_email: str, *, scopes: tuple[str, ...]) -> Credentials:
        assert scopes == (*SCOPES, 'https://www.googleapis.com/auth/gmail.compose')
        client.credentials = client.token(scopes=scopes)
        return ControlledGoogle.authorize(client, client_file, expected_email, scopes=scopes)
    monkeypatch.setattr(client, 'authorize', authorize)
    settings.GMAIL_CLIENT_FILE = str(tmp_path / 'oauth.json')
    monkeypatch.setattr(GmailCommand, 'service', lambda self: service)
    output = StringIO()
    call_command('gmail_connect', email=client.email, stdout=output)
    call_command('gmail_status', stdout=output)
    call_command('gmail_disconnect', stdout=output)
    assert client.email in output.getvalue() and 'SECRET' not in output.getvalue()
    client.authorization_error = True
    with pytest.raises(CommandError) as error:
        call_command('gmail_connect', email=client.email)
    assert 'SECRET' not in str(error.value)


def test_rejects_non_google_oauth_endpoints_before_consent(tmp_path: Path) -> None:
    path = tmp_path / 'client.json'
    path.write_text(json.dumps({'installed': {'auth_uri': 'https://attacker.test', 'token_uri': TOKEN_URL}}))
    with pytest.raises(GmailError, match='official Google'):
        GoogleClient().authorize(path, 'owner@example.com')


class ConsentBrowser:
    def __init__(self) -> None:
        self.query: dict[str, list[str]] = {}

    def open(self, url: str, **kwargs: Any) -> bool:
        self.query = parse_qs(urlparse(url).query)
        return True


class CallbackServer:
    server_port = 49800

    def __init__(self, app: Any, browser: ConsentBrowser, outcome: str) -> None:
        self.app = app
        self.browser = browser
        self.outcome = outcome
        self.closed = False
        self.timeout: int | None = None

    def handle_request(self) -> None:
        if self.outcome == 'timeout':
            return
        state = self.browser.query['state'][0] if self.outcome != 'wrong_state' else 'untrusted-state'
        query = {'state': state, 'error': 'access_denied'} if self.outcome == 'denied' else {'state': state, 'code': 'synthetic-code'}
        self.app.last_request_uri = 'http://127.0.0.1:49800/?' + urlencode(query)

    def server_close(self) -> None:
        self.closed = True


@pytest.mark.parametrize('outcome', ['success', 'wrong_state', 'denied', 'timeout', 'partial_scope'])
def test_real_oauth_library_enforces_callback_state_and_pkce(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, outcome: str) -> None:
    path = tmp_path / 'desktop.json'
    path.write_text(json.dumps({'installed': {'client_id': 'synthetic.apps.googleusercontent.com',
        'client_secret': 'SECRET-client', 'auth_uri': AUTH_URLS[0], 'token_uri': TOKEN_URL,
        'redirect_uris': ['http://localhost']}}))
    browser = ConsentBrowser()
    servers: list[CallbackServer] = []
    token_requests: list[dict[str, Any]] = []

    def server(host: str, port: int, app: Any, **kwargs: Any) -> CallbackServer:
        assert host == '127.0.0.1' and port == 0
        instance = CallbackServer(app, browser, outcome)
        servers.append(instance)
        return instance

    def token_request(self: Any, method: str, url: str, **kwargs: Any) -> requests.Response:
        assert url == TOKEN_URL and method.upper() == 'POST'
        token_requests.append(kwargs)
        data = dict(kwargs['data'])
        verifier = data['code_verifier']
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip('=')
        assert browser.query['code_challenge'] == [challenge]
        assert browser.query['code_challenge_method'] == ['S256']
        assert browser.query['access_type'] == ['offline']
        assert browser.query['scope'] == list(SCOPES)
        response = requests.Response()
        response.status_code = 200
        response._content = b''
        _ = response.content
        response.request = requests.Request('POST', url, data=kwargs['data']).prepare()
        response._content = json.dumps({'access_token': 'SECRET-access', 'refresh_token': 'SECRET-refresh',
            'token_type': 'Bearer', 'expires_in': 3600,
            'scope': 'different-scope' if outcome == 'partial_scope' else SCOPES[0]}).encode()
        return response

    monkeypatch.setattr('google_auth_oauthlib.flow.wsgiref.simple_server.make_server', server)
    monkeypatch.setattr('google_auth_oauthlib.flow.webbrowser.get', lambda *args: browser)
    monkeypatch.setattr(requests.Session, 'request', token_request)
    monkeypatch.setattr(GoogleClient, 'profile', lambda self, credentials: 'owner@example.com')
    store = KeychainStore(tmp_path / 'state', 'test-owner')
    monkeypatch.setattr(store, 'backend', MemoryKeychain())
    service = GmailService(store, GoogleClient(), tmp_path / 'state')
    if outcome == 'success':
        assert 'Connected Gmail' in service.connect(path, 'owner@example.com')
        assert store.load() is not None
    else:
        with pytest.raises(GmailError) as error:
            service.connect(path, 'owner@example.com')
        assert 'SECRET' not in str(error.value)
        assert store.load() is None
    assert servers[0].closed
    if outcome in ('wrong_state', 'denied', 'timeout'):
        assert token_requests == []


def test_profile_and_revoke_are_bounded_and_do_not_follow_redirects(monkeypatch: pytest.MonkeyPatch) -> None:
    requests_seen: list[tuple[str, str]] = []

    def request(method: str, url: str, **kwargs: Any) -> requests.Response:
        requests_seen.append((method, url))
        assert kwargs['timeout'] == HTTP_TIMEOUT_SECONDS and kwargs['allow_redirects'] is False
        response = requests.Response()
        response.status_code = 200
        response._content = b''
        _ = response.content
        if method == 'GET':
            assert url == PROFILE_URL and kwargs['headers']['Authorization'] == 'Bearer SECRET-access'
            response._content = b'{"emailAddress":"owner@example.com"}'
        else:
            assert url == REVOKE_URL and kwargs['data'] == {'token': 'SECRET-refresh'}
        return response

    monkeypatch.setattr(requests, 'get', lambda url, **kwargs: request('GET', url, **kwargs))
    monkeypatch.setattr(requests, 'post', lambda url, **kwargs: request('POST', url, **kwargs))
    client = GoogleClient()
    token = ControlledGoogle.token()
    assert client.profile(token) == 'owner@example.com'
    assert client.revoke(token)
    assert requests_seen == [('GET', PROFILE_URL), ('POST', REVOKE_URL)]


def test_oauth_token_exchange_has_finite_http_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    def send(self: Any, request: Any, **kwargs: Any) -> requests.Response:
        assert kwargs['timeout'] == HTTP_TIMEOUT_SECONDS
        return requests.Response()
    monkeypatch.setattr(requests.adapters.HTTPAdapter, 'send', send)
    TimeoutAdapter().send(requests.Request('POST', TOKEN_URL).prepare(), timeout=None)


@pytest.mark.parametrize('outcome', ['success', 'revoked', 'scope_changed'])
def test_google_refresh_transport_preserves_refresh_token_and_sanitizes_failures(connection: tuple[GmailService, ControlledGoogle, MemoryKeychain], tmp_path: Path, monkeypatch: pytest.MonkeyPatch, outcome: str) -> None:
    service, controlled, _ = connection
    controlled.credentials.expiry = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=1)
    service.connect(tmp_path / 'oauth.json', controlled.email)
    real_client = GoogleClient()
    monkeypatch.setattr(real_client, 'profile', lambda credentials: controlled.email)
    service.client = real_client
    calls: list[str] = []

    def refresh_request(self: Any, method: str, url: str, **kwargs: Any) -> requests.Response:
        assert url == TOKEN_URL and method == 'POST'
        assert kwargs['timeout'] == HTTP_TIMEOUT_SECONDS
        calls.append(url)
        payload = parse_qs(kwargs['data'].decode())
        assert payload['refresh_token'] == ['SECRET-refresh']
        response = requests.Response()
        response.status_code = 400 if outcome == 'revoked' else 200
        body = {'error': 'invalid_grant', 'error_description': 'SECRET provider detail'} if outcome == 'revoked' else {
            'access_token': 'SECRET-new', 'expires_in': 3600, 'token_type': 'Bearer',
            'scope': 'different-scope' if outcome == 'scope_changed' else SCOPES[0]}
        response._content = json.dumps(body).encode()
        return response

    monkeypatch.setattr(requests.Session, 'request', refresh_request)
    if outcome == 'success':
        assert 'verified' in service.status()
        loaded = service.store.load()
        assert loaded is not None
        assert loaded.credentials.token == 'SECRET-new' and loaded.credentials.refresh_token == 'SECRET-refresh'
    else:
        with pytest.raises(GmailError) as error:
            service.status()
        assert 'SECRET' not in str(error.value)
        loaded = service.store.load()
        assert loaded is not None and loaded.credentials.token == 'SECRET-access'
    assert calls == [TOKEN_URL]


def test_unreadable_active_state_can_still_be_disconnected(connection: tuple[GmailService, ControlledGoogle, MemoryKeychain], tmp_path: Path) -> None:
    service, client, backend = connection
    service.connect(tmp_path / 'oauth.json', client.email)
    (tmp_path / 'active-slot').write_text('corrupt')
    assert 'revocation' in service.disconnect()
    assert not backend.entries and service.status() == 'Gmail is not connected.'


def test_connection_is_not_available_to_another_owner(connection: tuple[GmailService, ControlledGoogle, MemoryKeychain], tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    service, client, backend = connection
    service.connect(tmp_path / 'oauth.json', client.email)
    other_store = KeychainStore(tmp_path / 'other-owner', 'other-owner')
    monkeypatch.setattr(other_store, 'backend', backend)
    other = GmailService(other_store, client, tmp_path / 'other-owner')
    assert other.status() == 'Gmail is not connected.'
    other.disconnect()
    assert 'verified' in service.status()


def test_mixed_desktop_web_config_cannot_bypass_endpoint_validation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / 'mixed.json'
    path.write_text(json.dumps({'installed': {'auth_uri': AUTH_URLS[0], 'token_uri': TOKEN_URL,
        'client_id': 'synthetic-client', 'client_secret': 'synthetic-secret'},
        'web': {'auth_uri': 'https://attacker.test/consent', 'token_uri': 'https://attacker.test/token',
                'client_id': 'wrong-client', 'client_secret': 'wrong-secret'}}))
    opened: list[str] = []
    monkeypatch.setattr('google_auth_oauthlib.flow.InstalledAppFlow.from_client_config',
                        lambda *args, **kwargs: opened.append('unexpected-flow'))
    with pytest.raises(GmailError, match='Desktop OAuth'):
        GoogleClient().authorize(path, 'owner@example.com')
    assert opened == []


def test_corrupt_state_has_actionable_reconnect_recovery(connection: tuple[GmailService, ControlledGoogle, MemoryKeychain], tmp_path: Path) -> None:
    service, client, _ = connection
    service.connect(tmp_path / 'oauth.json', client.email)
    (tmp_path / 'active-slot').write_text('corrupt')
    with pytest.raises(GmailError, match='gmail_disconnect'):
        service.connect(tmp_path / 'oauth.json', client.email)
    service.disconnect()
    assert 'Connected Gmail' in service.connect(tmp_path / 'oauth.json', client.email)


def test_readonly_connection_can_upgrade_without_losing_previous_grant(connection: tuple[GmailService, ControlledGoogle, MemoryKeychain], tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    service, client, _ = connection
    service.connect(tmp_path / 'oauth.json', client.email)
    original = service.store.load()
    assert original is not None
    def authorize(client_file: Path, expected_email: str, *, scopes: tuple[str, ...]) -> Credentials:
        assert scopes == (SCOPES[0], 'https://www.googleapis.com/auth/gmail.compose')
        return Credentials('compose-token', refresh_token='compose-refresh', token_uri=TOKEN_URL,
            client_id='test', client_secret='synthetic-secret', scopes=list(scopes), expiry=datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=1))
    monkeypatch.setattr(client, 'authorize', authorize)
    assert 'draft' in service.connect(tmp_path / 'oauth.json', client.email, compose=True).lower()
    assert 'verified' in service.status()


@pytest.mark.parametrize('failure', ['cancel', 'readonly', 'account'])
def test_failed_draft_upgrade_preserves_reading(connection: tuple[GmailService, ControlledGoogle, MemoryKeychain], tmp_path: Path, failure: str) -> None:
    service, client, _ = connection
    service.connect(tmp_path / 'oauth.json', client.email)
    original = service.store.load()
    assert original is not None
    if failure == 'cancel':
        client.authorization_error = True
    elif failure == 'account':
        client.credentials = client.token(scopes=(*SCOPES, 'https://www.googleapis.com/auth/gmail.compose'))
        client.email = 'different@example.com'
    with pytest.raises(GmailError):
        service.connect(tmp_path / 'oauth.json', original.email, compose=True)
    current = service.store.load()
    assert current is not None
    assert current.credentials.to_json() == original.credentials.to_json()


def test_disconnect_invalidates_monitors_before_credentials_are_disabled(connection: tuple[GmailService, ControlledGoogle, MemoryKeychain]) -> None:
    service, _, _ = connection
    events: list[str] = []
    service.disable_monitoring = lambda: events.append('stopped')
    service.disconnect()
    assert events == ['stopped']


def test_successful_refresh_removes_obsolete_keychain_slot(connection: tuple[GmailService, ControlledGoogle, MemoryKeychain], tmp_path: Path) -> None:
    service, google, keychain = connection
    service.connect(tmp_path / 'client.json', google.email)
    google.credentials.expiry = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=1)
    service.store.save(Connection(google.email, google.credentials))
    service.status()
    assert len(keychain.entries) == 1
    assert 'SECRET-refreshed' in next(iter(keychain.entries.values()))


def test_obsolete_slot_cleanup_failure_does_not_break_active_grant(connection: tuple[GmailService, ControlledGoogle, MemoryKeychain], tmp_path: Path) -> None:
    service, google, keychain = connection
    service.connect(tmp_path / 'client.json', google.email)
    keychain.fail_delete = True
    assert 'Connected Gmail' in service.connect(tmp_path / 'client.json', google.email)
    assert 'connected' in service.status()
