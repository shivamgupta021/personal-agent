from pathlib import Path
from typing import Any

from django.conf import settings
from django.core.management.base import CommandError, CommandParser

from gmail.commands import GmailCommand
from gmail.service import GmailService


class Command(GmailCommand):
    help = 'Authorize Gmail reading and draft creation together using desktop browser consent.'

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument('--email', required=True, help='Expected Gmail account address.')
        parser.add_argument('--replace-account', action='store_true')
        parser.add_argument('--compose', action='store_true', default=True, help='Reading and draft creation are requested together by default.')

    def execute_operation(self, service: GmailService, options: dict[str, Any]) -> str:
        if not settings.GMAIL_CLIENT_FILE:
            raise CommandError('Set GMAIL_CLIENT_FILE in .env to the ignored Google Desktop OAuth JSON file.')
        client_file = Path(settings.GMAIL_CLIENT_FILE).expanduser()
        if not client_file.is_absolute():
            client_file = settings.BASE_DIR / client_file
        return service.connect(client_file, options['email'], replace_account=options['replace_account'], compose=options['compose'])
