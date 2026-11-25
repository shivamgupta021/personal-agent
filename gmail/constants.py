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

ASSISTANCE_REQUEST_LIMIT = 5
ASSISTANCE_OUTPUT_TOKENS = 1200
ASSISTANCE_TIMEOUT_SECONDS = 40

MAX_MESSAGE_TEXT_CHARS = 3000

COMPOSE_SCOPE = 'https://www.googleapis.com/auth/gmail.compose'
COMPOSE_SCOPES = (READONLY_SCOPE, COMPOSE_SCOPE)
DRAFT_MAX_BODY_CHARS = 12000
DRAFT_MAX_SUBJECT_CHARS = 300
DRAFT_MAX_OUTPUT_TOKENS = 2500

DRAFT_RECONCILE_LIMIT = 20
DRAFT_MARKER_HEADER = 'X-Personal-Agent-Export'

MAX_ADDRESS_HEADER_CHARS = 1000
MAX_REFERENCE_HEADER_CHARS = 4000
DRAFT_MAX_QUOTE_CHARS = 2000
DRAFT_MAX_SOURCES = 20
DRAFT_MAX_QUESTION_CHARS = 400

CONNECT_MESSAGE = ("To find the relevant emails, I need to connect your Gmail first. "
    "Which Gmail address should I connect? I’ll continue with your request after you approve access.")
