READONLY_SCOPE = 'https://www.googleapis.com/auth/gmail.readonly'
SCOPES = (READONLY_SCOPE,)
HTTP_TIMEOUT_SECONDS = 20
CONSENT_TIMEOUT_SECONDS = 180
PROFILE_URL = 'https://gmail.googleapis.com/gmail/v1/users/me/profile'
TOKEN_URL = 'https://oauth2.googleapis.com/token'
REVOKE_URL = 'https://oauth2.googleapis.com/revoke'
AUTH_URLS = ('https://accounts.google.com/o/oauth2/auth', 'https://accounts.google.com/o/oauth2/v2/auth')
KEYCHAIN_SERVICE = 'personal-agent.gmail'

API_URL = 'https://gmail.googleapis.com/gmail/v1/users/me'
MAX_CANDIDATES = 5
MAX_MESSAGES = 20
MAX_TEXT_CHARS = 12000
MAX_RESPONSE_BYTES = 1000000
MAX_QUERY_CHARS = 500
MAX_PREVIEW_CHARS = 240
MAX_MIME_PARTS = 100

MAX_HEADERS = 100

PLANNER_MAX_QUERIES = 3
PLANNER_RETRIES = 1
PLANNER_REQUEST_LIMIT = 2
PLANNER_TIMEOUT_SECONDS = 20
PLANNER_MAX_OUTPUT_TOKENS = 500
