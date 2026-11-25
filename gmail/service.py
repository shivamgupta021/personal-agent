"""One owner's locked Gmail connection lifecycle, with sanitized public failures."""

from contextlib import contextmanager
import fcntl
import os
from pathlib import Path
from collections.abc import Iterator, Callable

from google.oauth2.credentials import Credentials

from gmail.client import GoogleClient
from gmail.constants import SCOPES, COMPOSE_SCOPES, COMPOSE_SCOPE, TOKEN_URL
from gmail.credentials import Connection, CredentialStore
from gmail.errors import GmailError, GmailDisconnected, GmailBusy, GmailComposeRequired


class GmailService:
    def __init__(self, store: CredentialStore, client: GoogleClient, directory: Path, *, disable_monitoring: Callable[[], None] | None = None) -> None:
        self.store = store
        self.client = client
        self.directory = directory
        self.disable_monitoring = disable_monitoring

    @contextmanager
    def operation(self) -> Iterator[None]:
        descriptor: int | None = None
        try:
            self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            descriptor = os.open(self.directory / 'connection.lock', os.O_CREAT | os.O_RDWR, 0o600)
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise GmailBusy('Another Gmail operation is running. Try again when it finishes.') from None
            yield
        except GmailError:
            raise
        except Exception:
            raise GmailError('Gmail operation failed. Check configuration, Keychain access or reconnect; no credentials were printed.') from None
        finally:
            if descriptor is not None:
                os.close(descriptor)

    @staticmethod
    def validate(credentials: Credentials, *, require_compose: bool = False) -> None:
        scopes = credentials.granted_scopes if credentials.granted_scopes is not None else credentials.scopes
        if set(scopes or ()) not in (set(SCOPES), set(COMPOSE_SCOPES)):
            raise GmailError('Gmail grant is incomplete or broader than supported. Reconnect with the requested permissions.')
        if require_compose and COMPOSE_SCOPE not in (scopes or ()):
            raise GmailComposeRequired('Gmail is connected for reading only. Enable draft creation through Google consent; your current reading connection is preserved.')
        if not credentials.refresh_token or credentials.token_uri != TOKEN_URL:
            raise GmailError('Gmail offline credentials are missing or invalid. Reconnect Gmail.')

    def connect(self, client_file: Path, expected_email: str, *, replace_account: bool = False, compose: bool = False) -> str:
        with self.operation():
            if '@' not in expected_email or any(c.isspace() for c in expected_email):
                raise GmailError('Provide the expected Gmail address using --email.')
            try:
                previous = self.store.load()
            except Exception:
                raise GmailError('Existing Gmail credentials could not be read. Unlock Keychain and retry, or run gmail_disconnect before reconnecting.') from None
            if previous and previous.email.casefold() != expected_email.casefold() and not replace_account:
                raise GmailError('A different mailbox is connected. Use --replace-account to change it explicitly.')
            credentials = self.client.authorize(client_file, expected_email, scopes=COMPOSE_SCOPES) if compose else self.client.authorize(client_file, expected_email)
            self.validate(credentials, require_compose=compose)
            granted = credentials.granted_scopes if credentials.granted_scopes is not None else credentials.scopes
            if not compose and set(granted or ()) != set(SCOPES):
                raise GmailError('Unexpected Gmail permissions; the previous connection was preserved. Request draft permission explicitly.')
            actual_email = self.client.profile(credentials)
            if actual_email.casefold() != expected_email.casefold():
                raise GmailError('The selected Google account does not match --email. The previous connection was preserved.')
            if previous and previous.email.casefold() != actual_email.casefold() and self.disable_monitoring:
                self.disable_monitoring()
            self.store.save(Connection(actual_email, credentials))
            return f'Connected Gmail: {actual_email}. ' + ('Reading and draft creation enabled.' if compose else 'Read-only access enabled.')

    @contextmanager
    def connected(self) -> Iterator[Connection]:
        """Keep credential refresh/account validation and mailbox reads under one lease."""
        with self.operation():
            connection = self.store.load()
            if connection is None:
                raise GmailDisconnected('Gmail is not connected.')
            self.validate(connection.credentials)
            if not connection.credentials.valid:
                self.client.refresh(connection.credentials)
                self.validate(connection.credentials)
                self.store.save(connection)
            if self.client.profile(connection.credentials).casefold() != connection.email.casefold():
                raise GmailError('Gmail account verification changed. Reconnect before using it.')
            yield connection

    def require_connection(self) -> None:
        """Reject a missing local grant before spending tokens planning a search."""
        with self.operation():
            if self.store.load() is None:
                raise GmailDisconnected('Gmail is not connected.')

    def status(self) -> str:
        # A disconnected status is informational, not a failed operation.
        with self.operation():
            if self.store.load() is None:
                return 'Gmail is not connected.'
        with self.connected() as connection:
            scopes = connection.credentials.granted_scopes or connection.credentials.scopes or ()
            capability = 'Reading and draft creation' if COMPOSE_SCOPE in scopes else 'Read-only access'
            return f'Gmail connected: {connection.email}. {capability} verified; no messages were fetched.'

    def disconnect(self) -> str:
        with self.operation():
            try:
                connection = self.store.load()
            except Exception:
                connection = None
            if self.disable_monitoring:
                self.disable_monitoring()
            self.store.disable()
            revoked = False
            if connection is not None:
                try:
                    revoked = self.client.revoke(connection.credentials)
                except Exception:
                    revoked = False
            try:
                self.store.clear()
            except Exception:
                raise GmailError('Gmail access is disabled locally, but Keychain cleanup failed. Retry disconnect; review Google account access too.') from None
            if connection is None:
                return 'Gmail is disconnected locally. No active grant was available to verify Google revocation; review Google account access if needed.'
            if revoked:
                return 'Gmail disconnected locally; Google accepted revocation. Existing case evidence and Gmail drafts were not deleted.'
            return 'Gmail disconnected locally. Google revocation was not confirmed; remove this app in your Google account permissions. Case evidence and Gmail drafts were not deleted.'
