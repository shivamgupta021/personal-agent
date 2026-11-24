"""Mac Keychain credentials with a token-free, atomic active-slot pointer."""

from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import sys
from typing import Protocol
from uuid import uuid4

from google.oauth2.credentials import Credentials
from keyring.backends.macOS import Keyring

from gmail.constants import KEYCHAIN_SERVICE
from gmail.errors import GmailError


@dataclass
class Connection:
    email: str
    credentials: Credentials = field(repr=False)


class CredentialStore(Protocol):
    def load(self) -> Connection | None: ...
    def save(self, connection: Connection) -> None: ...
    def disable(self) -> None: ...
    def clear(self) -> None: ...


class KeychainStore:
    def __init__(self, directory: Path, account: str) -> None:
        self.directory = directory
        self.pointer = directory / 'active-slot'
        self.account = account
        self.backend = Keyring()

    def load(self) -> Connection | None:
        if not self.pointer.exists():
            return None
        slot = self.pointer.read_text()
        if slot not in ('0', '1'):
            raise GmailError('Gmail credential state is invalid. Run gmail_disconnect, then reconnect Gmail.')
        raw = self.backend.get_password(KEYCHAIN_SERVICE, self.account + ':' + slot)
        if raw is None:
            raise GmailError('Gmail credentials are unavailable. Run gmail_disconnect, then reconnect Gmail.')
        data = json.loads(raw)
        return Connection(data['email'], Credentials.from_authorized_user_info(json.loads(data['credentials'])))

    def save(self, connection: Connection) -> None:
        if sys.platform != 'darwin':
            raise GmailError('Gmail requires macOS Keychain; no plaintext fallback is enabled.')
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        slot = '1' if self.pointer.exists() and self.pointer.read_text() == '0' else '0'
        raw = json.dumps({'email': connection.email, 'credentials': connection.credentials.to_json()})
        # Only replace the inactive slot. A failed write leaves the active grant intact.
        self.backend.set_password(KEYCHAIN_SERVICE, self.account + ':' + slot, raw)
        temporary = self.directory / ('pointer-' + uuid4().hex)
        try:
            with os.fdopen(os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'w') as output:
                output.write(slot)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, self.pointer)
        finally:
            temporary.unlink(missing_ok=True)

    def disable(self) -> None:
        self.pointer.unlink(missing_ok=True)

    def clear(self) -> None:
        # Both slots are known even after a failed save or local disable.
        for slot in ('0', '1'):
            key = self.account + ':' + slot
            if self.backend.get_password(KEYCHAIN_SERVICE, key) is not None:
                self.backend.delete_password(KEYCHAIN_SERVICE, key)
