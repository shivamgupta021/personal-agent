"""Subscription authentication only: no agent harness, no API-key fallback."""

import asyncio
import fcntl
import json
import os
import tempfile
import uuid
import webbrowser
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict
from pathlib import Path

from pydantic_ai.providers.openai_chatgpt import (
    OpenAIChatGPTCredentials,
    OpenAIChatGPTOAuthFlow,
    OpenAIChatGPTProvider,
)


def _write_private(path: Path, content: str) -> None:
    """Replace the whole record atomically, with owner-only permissions from creation."""
    descriptor, temporary = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(descriptor, "w") as file:
            file.write(content)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class CredentialStore:
    def __init__(self, directory: Path) -> None:
        self.path = directory / "credentials.json"

    async def load(self) -> OpenAIChatGPTCredentials:
        if not self.path.exists():
            raise RuntimeError("Run the login command first.")
        return OpenAIChatGPTCredentials(**json.loads(self.path.read_text()))

    async def save(self, credentials: OpenAIChatGPTCredentials) -> None:
        _write_private(self.path, json.dumps(asdict(credentials)))


class SubscriptionSession:
    """Own sign-in, credential access and exclusive use of one local session."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self.credentials = CredentialStore(directory)

    @contextmanager
    def locked(self) -> Iterator[None]:
        directory = self.directory
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        directory.chmod(0o700)
        descriptor = os.open(directory / "session.lock", os.O_CREAT | os.O_RDWR, 0o600)
        try:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise RuntimeError("Another command is using this subscription session.") from None
            yield
        finally:
            os.close(descriptor)

    async def login(self) -> None:
        directory = self.directory
        host_file = directory / "host-id"
        if not host_file.exists():
            _write_private(host_file, uuid.uuid4().urn)
        store = self.credentials
        previous = await store.load() if store.path.exists() else None
        registration = directory / "client-id"
        client_id = previous.client_id if previous else (
            registration.read_text() if registration.exists() else None
        )
        flow = OpenAIChatGPTOAuthFlow(
            app_name="Personal Agent Learning Lab", host_id=host_file.read_text(),
            client_id=client_id, id_token_hint=previous.id_token if previous else None,
        )
        print("Continue with ChatGPT in your browser. Waiting up to 5 minutes; Ctrl-C cancels.")
        try:
            async with asyncio.timeout(300):
                callback = asyncio.create_task(flow.exchange_code_from_callback())
                try:
                    # Let the helper start its loopback listener before opening the browser.
                    await asyncio.sleep(0.2)
                    if callback.done():
                        await callback  # surface a port-binding failure before opening the browser
                    if not await asyncio.to_thread(webbrowser.open, flow.authorization_url()):
                        raise RuntimeError("Could not open the browser. Run login from a desktop terminal.")
                    credentials = await callback
                finally:
                    if not callback.done():
                        callback.cancel()
                        await asyncio.gather(callback, return_exceptions=True)
            await store.save(credentials)
            if not credentials.plan_usage_granted:
                raise RuntimeError("Sign-in completed, but plan usage was not granted. Run login again.")
            print("Subscription sign-in saved. Use models to list your available models.")
        finally:
            if flow.client_id:
                _write_private(registration, flow.client_id)

    def provider(self) -> OpenAIChatGPTProvider:
        return OpenAIChatGPTProvider(credential_source=self.credentials)
