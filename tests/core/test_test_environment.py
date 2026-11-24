"""Regression tests must not publish controlled runs into the live Langfuse project."""

from django.conf import settings


def test_live_tracing_credentials_are_disabled_before_django_setup() -> None:
    live_keys_loaded = bool(settings.LANGFUSE_PUBLIC_KEY or settings.LANGFUSE_SECRET_KEY)
    assert not live_keys_loaded, 'Live tracing credentials loaded before test isolation'
