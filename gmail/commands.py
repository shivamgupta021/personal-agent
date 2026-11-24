"""Shared construction and sanitized command boundary."""

from typing import Any

from django.core.management.base import BaseCommand, CommandError

from gmail.factory import GmailFactory
from gmail.errors import GmailError
from gmail.service import GmailService


class GmailCommand(BaseCommand):
    def service(self) -> GmailService:
        return GmailFactory.connection()

    def handle(self, *args: Any, **options: Any) -> str:
        try:
            return self.execute_operation(self.service(), options)
        except GmailError as error:
            raise CommandError(str(error)) from None

    def execute_operation(self, service: GmailService, options: dict[str, Any]) -> str:
        raise NotImplementedError
