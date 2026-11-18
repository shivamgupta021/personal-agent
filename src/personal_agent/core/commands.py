"""Shared sync/async and error boundary for Django management commands."""

import asyncio
from collections.abc import Coroutine
from typing import Any, TypeVar

from django.core.management.base import CommandError

from personal_agent.core.errors import user_error_message
from personal_agent.core.tracing import tracing

Result = TypeVar('Result')


def run_async(operation: Coroutine[Any, Any, Result]) -> Result:
    try:
        return asyncio.run(operation)
    except KeyboardInterrupt:
        raise CommandError('Cancelled.', returncode=130) from None
    except Exception as error:
        raise CommandError(user_error_message(error)) from None
    finally:
        tracing.flush()
