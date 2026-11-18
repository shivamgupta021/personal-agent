"""Local Django settings. No web endpoints or persistent case models yet."""

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[3]
SECRET_KEY = os.getenv('DJANGO_SECRET_KEY', 'local-command-only-development-key')
DEBUG = False
ALLOWED_HOSTS = ['localhost', '127.0.0.1']
INSTALLED_APPS = [
    'personal_agent.core.apps.CoreConfig',
    'personal_agent.subscriptions.apps.SubscriptionsConfig',
    'personal_agent.research.apps.ResearchConfig',
]
ROOT_URLCONF = 'personal_agent.config.urls'
WSGI_APPLICATION = 'personal_agent.config.wsgi.application'
ASGI_APPLICATION = 'personal_agent.config.asgi.application'
DATABASES = {'default': {'ENGINE': 'django.db.backends.dummy'}}
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'
USE_TZ = True
TIME_ZONE = 'Asia/Kolkata'

SUBSCRIPTION_STATE_DIR = BASE_DIR / '.local' / 'chatgpt'
FIRECRAWL_BASE_URL = os.getenv('FIRECRAWL_BASE_URL', 'http://127.0.0.1:3002')
FIRECRAWL_API_KEY = os.getenv('FIRECRAWL_API_KEY', '')
LANGFUSE_BASE_URL = os.getenv('LANGFUSE_BASE_URL', 'http://127.0.0.1:3000')
LANGFUSE_PUBLIC_KEY = os.getenv('LANGFUSE_PUBLIC_KEY', '')
LANGFUSE_SECRET_KEY = os.getenv('LANGFUSE_SECRET_KEY', '')
LANGFUSE_TIMEOUT_SECONDS = 5.0

LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {'console': {'format': '%(levelname)s %(name)s: %(message)s'}},
    'handlers': {'console': {'class': 'logging.StreamHandler', 'formatter': 'console'}},
    'loggers': {'personal_agent': {'handlers': ['console'], 'level': 'INFO', 'propagate': False}},
}
os.environ.setdefault('PYDANTIC_AI_NO_BANNER', '1')
