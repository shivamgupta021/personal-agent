"""One owner's locked Gmail connection lifecycle, with sanitized public failures."""

from contextlib import contextmanager
import fcntl
import os
from pathlib import Path
from collections.abc import Iterator

from google.oauth2.credentials import Credentials

from gmail.client import GoogleClient
from gmail.constants import SCOPES, TOKEN_URL
from gmail.credentials import Connection, CredentialStore
from gmail.errors import GmailError


class GmailService:
    def __init__(self, store: CredentialStore, client: GoogleClient, directory: Path) -> None:
        self.store = store
        self.client = client
        self.directory = directory

    @contextmanager
    def operation(self) -> Iterator[None]:
        descriptor: int | None = None
        try:
            self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            descriptor = os.open(self.directory / 'connection.lock', os.O_CREAT | os.O_RDWR, 0o600)
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise GmailError('Another Gmail operation is running. Try again when it finishes.') from None
            yield
        except GmailError:
            raise
        except Exception:
            raise GmailError('Gmail operation failed. Check configuration, Keychain access or reconnect; no credentials were printed.') from None
        finally:
            if descriptor is not None:
                os.close(descriptor)

    @staticmethod
    def validate(credentials: Credentials) -> None:
        scopes = credentials.granted_scopes if credentials.granted_scopes is not None else credentials.scopes
        if set(scopes or ()) != set(SCOPES):
            raise GmailError('Gmail requires exactly the read-only grant for this milestone. Review this app in your Google account and reconnect.')
        if not credentials.refresh_token or credentials.token_uri != TOKEN_URL:
            raise GmailError('Gmail offline credentials are missing or invalid. Reconnect Gmail.')

    def connect(self, client_file: Path, expected_email: str, *, replace_account: bool = False) -> str:
        with self.operation():
            if '@' not in expected_email or any(c.isspace() for c in expected_email):
                raise GmailError('Provide the expected Gmail address using --email.')
            previous = self.store.load()
            if previous and previous.email.casefold() != expected_email.casefold() and not replace_account:
                raise GmailError('A different mailbox is connected. Use --replace-account to change it explicitly.')
            credentials = self.client.authorize(client_file, expected_email)
            self.validate(credentials)
            actual_email = self.client.profile(credentials)
            if actual_email.casefold() != expected_email.casefold():
                raise GmailError('The selected Google account does not match --email. The previous connection was preserved.')
            self.store.save(Connection(actual_email, credentials))
            return f'Connected Gmail: {actual_email}. Read-only access; no messages were fetched.'

    def status(self) -> str:
        with self.operation():
            connection = self.store.load()
            if connection is None:
                return 'Gmail is not connected.'
            credentials = connection.credentials
            self.validate(credentials)
            refreshed = not credentials.valid
            if refreshed:
                self.client.refresh(credentials)
                self.validate(credentials)
            actual_email = self.client.profile(credentials)
            if actual_email.casefold() != connection.email.casefold():
                raise GmailError('Gmail account verification changed. Reconnect before using it.')
            if refreshed:
                self.store.save(connection)
            return f'Gmail connected: {actual_email}. Read-only access verified; no messages were fetched.'

    def disconnect(self) -> str:
        with self.operation():
            try:
                connection = self.store.load()
            except Exception:
                connection = None
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
