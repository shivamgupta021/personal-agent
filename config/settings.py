"""Local Django settings. No web endpoints or persistent case models yet."""

import os
from pathlib import Path

from dotenv import load_dotenv

from subscriptions.constants import DEFAULT_MODEL

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / '.env', override=False)

SECRET_KEY = os.getenv('DJANGO_SECRET_KEY', 'local-command-only-development-key')
DEBUG = False
ALLOWED_HOSTS = ['localhost', '127.0.0.1']
INSTALLED_APPS = [
    'core.apps.CoreConfig',
    'conversations.apps.ConversationsConfig',
    'preferences.apps.PreferencesConfig',
    'subscriptions.apps.SubscriptionsConfig',
    'research.apps.ResearchConfig',
    'telegram_bot.apps.TelegramBotConfig',
]
ROOT_URLCONF = 'config.urls'
WSGI_APPLICATION = 'config.wsgi.application'
ASGI_APPLICATION = 'config.asgi.application'
DATABASES = {'default': {
    'ENGINE': 'django.db.backends.postgresql',
    'NAME': os.getenv('POSTGRES_DB', 'personal_agent'),
    'USER': os.getenv('POSTGRES_USER', 'personal_agent'),
    'PASSWORD': os.getenv('POSTGRES_PASSWORD', ''),
    'HOST': os.getenv('POSTGRES_HOST', '127.0.0.1'),
    'PORT': os.getenv('POSTGRES_PORT', '55432'),
}}
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'
USE_TZ = True
TIME_ZONE = 'Asia/Kolkata'

SIMPLE_QUERY_MODEL = os.getenv('SIMPLE_QUERY_MODEL', DEFAULT_MODEL)
COMPLEX_QUERY_MODEL = os.getenv('COMPLEX_QUERY_MODEL', DEFAULT_MODEL)

TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN', '')
TELEGRAM_OWNER_ID = os.getenv('TELEGRAM_OWNER_ID', '')
TELEGRAM_STATE_DIR = BASE_DIR / '.local' / 'telegram'

SUBSCRIPTION_STATE_DIR = BASE_DIR / '.local' / 'chatgpt'
FIRECRAWL_BASE_URL = os.getenv('FIRECRAWL_BASE_URL', 'http://127.0.0.1:3002')
FIRECRAWL_API_KEY = os.getenv('FIRECRAWL_API_KEY', '')
LANGFUSE_BASE_URL = os.getenv('LANGFUSE_BASE_URL', 'http://127.0.0.1:3000')
LANGFUSE_PUBLIC_KEY = os.getenv('LANGFUSE_PUBLIC_KEY', '')
LANGFUSE_SECRET_KEY = os.getenv('LANGFUSE_SECRET_KEY', '')
LANGFUSE_CAPTURE_CONTENT = os.getenv('LANGFUSE_CAPTURE_CONTENT', 'false').strip().lower() == 'true'
LANGFUSE_TIMEOUT_SECONDS = 5.0

LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {'console': {'format': '%(levelname)s %(name)s: %(message)s'}},
    'handlers': {'console': {'class': 'logging.StreamHandler', 'formatter': 'console'}},
    'loggers': {
        name: {'handlers': ['console'], 'level': 'INFO', 'propagate': False}
        for name in ('core', 'research', 'subscriptions', 'telegram_bot')
    },
}
os.environ.setdefault('PYDANTIC_AI_NO_BANNER', '1')
