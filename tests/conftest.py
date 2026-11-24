import asyncio
from collections.abc import Iterator

import pytest
from asgiref.sync import sync_to_async
from django.db import connections


@pytest.fixture(autouse=True)
def close_async_connections() -> Iterator[None]:
    yield
    asyncio.run(sync_to_async(connections.close_all)())
