from typing import Any

from gmail.commands import GmailCommand
from gmail.service import GmailService


class Command(GmailCommand):
    help = 'Verify Gmail connection and refresh credentials if necessary; never fetch messages.'

    def execute_operation(self, service: GmailService, options: dict[str, Any]) -> str:
        return service.status()
