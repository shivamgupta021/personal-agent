from typing import Any

from gmail.commands import GmailCommand
from gmail.service import GmailService


class Command(GmailCommand):
    help = 'Disable local Gmail access, remove credentials and attempt Google grant revocation.'

    def execute_operation(self, service: GmailService, options: dict[str, Any]) -> str:
        return service.disconnect()
