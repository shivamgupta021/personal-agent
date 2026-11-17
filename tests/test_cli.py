import os
import subprocess
import sys
from pathlib import Path


def test_offline_demo_exposes_the_loop_without_credentials() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "personal_agent", "demo"],
        env={**os.environ, "PYTHONPATH": "src"}, capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0, result.stderr
    assert "OFFLINE" in result.stdout
    assert "tool_call" in result.stdout
    assert "tool_result" in result.stdout
    assert "INR 1200" in result.stdout
    assert "answered" in result.stdout


def test_live_mode_requires_its_own_login_and_never_falls_back_to_api_key(tmp_path: Path) -> None:
    result = subprocess.run(
        [sys.executable, "-m", "personal_agent", "live", "--model", "test-model",
         "--state-dir", str(tmp_path)],
        env={**os.environ, "PYTHONPATH": "src", "OPENAI_API_KEY": "must-not-be-used"},
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 1
    assert "Run the login command first" in result.stderr
    assert "must-not-be-used" not in result.stdout + result.stderr
