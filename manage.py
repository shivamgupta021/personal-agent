"""Django's command entry point for the local assistant."""

import os
import sys
from pathlib import Path

from django.core.management import execute_from_command_line

sys.path.insert(0, str(Path(__file__).resolve().parent / 'src'))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'personal_agent.config.settings')

if __name__ == '__main__':
    execute_from_command_line(sys.argv)
