import os
import subprocess
import sys
from pathlib import Path


def test_research_requires_its_own_login_and_never_falls_back_to_api_key(tmp_path: Path) -> None:
    result = subprocess.run(
        [sys.executable, "manage.py", "research", "Find public documentation", "--model", "test-model",
         "--state-dir", str(tmp_path)],
        env={**os.environ, "OPENAI_API_KEY": "must-not-be-used"},
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 1
    assert "Run the login command first" in result.stderr
    assert "must-not-be-used" not in result.stdout + result.stderr
