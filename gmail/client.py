"""Only desktop consent, refresh, profile and revocation; no mailbox or send API."""

import json
from functools import partial
from pathlib import Path
from typing import Any, cast

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
import requests
from requests.adapters import HTTPAdapter

from gmail import constants
from gmail.errors import GmailError


class TimeoutAdapter(HTTPAdapter):
    def send(self, request: requests.PreparedRequest, stream: bool = False, timeout: Any = None,
             verify: Any = True, cert: Any = None, proxies: Any = None) -> requests.Response:
        return super().send(request, stream=stream, timeout=constants.HTTP_TIMEOUT_SECONDS,
                            verify=verify, cert=cert, proxies=proxies)


class GoogleClient:
    def authorize(self, client_file: Path, expected_email: str, *, scopes: tuple[str, ...] = constants.SCOPES) -> Credentials:
        configuration = json.loads(client_file.read_text())
        installed = configuration.get('installed', {})
        if ('web' in configuration or installed.get('auth_uri') not in constants.AUTH_URLS
                or installed.get('token_uri') != constants.TOKEN_URL
                or not installed.get('client_id') or not installed.get('client_secret')):
            raise GmailError('Provide a Google Desktop OAuth client configuration with official Google endpoints.')
        flow = InstalledAppFlow.from_client_config({'installed': installed}, scopes=list(scopes),
                                                   autogenerate_code_verifier=True)
        flow.oauth2session.mount('https://', TimeoutAdapter())
        try:
            return cast(Credentials, flow.run_local_server(host='127.0.0.1', bind_addr='127.0.0.1', port=0,
                timeout_seconds=constants.CONSENT_TIMEOUT_SECONDS, authorization_prompt_message=None,
                success_message='Consent received. Return to the assistant to check connection verification.',
                prompt='consent', login_hint=expected_email))
        finally:
            flow.oauth2session.close()

    def refresh(self, credentials: Credentials) -> None:
        with requests.Session() as session:
            credentials.refresh(partial(Request(session=session), timeout=constants.HTTP_TIMEOUT_SECONDS))

    def profile(self, credentials: Credentials) -> str:
        if not isinstance(credentials.token, str) or not credentials.token:
            raise GmailError('Gmail access credentials are missing. Reconnect Gmail.')
        response = requests.get(constants.PROFILE_URL, headers={'Authorization': 'Bearer ' + credentials.token},
                                timeout=constants.HTTP_TIMEOUT_SECONDS, allow_redirects=False)
        try:
            if response.status_code != 200:
                raise GmailError('Gmail profile verification failed. Check API access or reconnect Gmail.')
            email = response.json().get('emailAddress')
            if not isinstance(email, str) or '@' not in email:
                raise GmailError('Google returned an invalid Gmail profile.')
            return email
        finally:
            response.close()

    def revoke(self, credentials: Credentials) -> bool:
        response = requests.post(constants.REVOKE_URL, data={'token': credentials.refresh_token or credentials.token},
                                 timeout=constants.HTTP_TIMEOUT_SECONDS, allow_redirects=False)
        try:
            return response.status_code == 200
        finally:
            response.close()
