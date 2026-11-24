import os
import subprocess
import sys

import pytest


@pytest.mark.parametrize('command', ['telegram', 'monitor'])
def test_missing_owner_configuration_fails_before_network_calls(command: str) -> None:
    result = subprocess.run(
        [sys.executable, 'manage.py', command],
        env={**os.environ, 'TELEGRAM_BOT_TOKEN': '123:PRIVATE_TOKEN', 'TELEGRAM_OWNER_ID': '',
             'LANGFUSE_PUBLIC_KEY': '', 'LANGFUSE_SECRET_KEY': ''},
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 1
    assert 'TELEGRAM_OWNER_ID' in result.stderr
    assert 'PRIVATE_TOKEN' not in result.stdout + result.stderr
