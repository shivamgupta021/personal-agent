"""Allow python -m personal_agent to use the same Django commands as manage.py."""

import os
import sys

from django.core.management import execute_from_command_line

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'personal_agent.config.settings')

if __name__ == '__main__':
    execute_from_command_line(sys.argv)
