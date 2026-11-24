"""Correspondence search excludes drafts and avoids duplicate thread reads."""
from pathlib import Path

import httpx2

from gmail.mailbox import Mailbox
from tests.telegram_bot.test_gmail import MailHarness


class SearchHarness(MailHarness):
    def __init__(self, directory: Path) -> None:
        super().__init__(directory)
        self.metadata_reads = 0
        self.query = ''

    def google(self, request: httpx2.Request) -> httpx2.Response:
        if request.url.path.endswith('/messages'):
            self.query = request.url.params['q']
            return httpx2.Response(200, json={'messages': [
                {'id': 'abc1', 'threadId': 'def1'}, {'id': 'abc2', 'threadId': 'def1'}]})
        self.metadata_reads += 1
        return super().google(request)


def test_search_filters_drafts_and_fetches_each_thread_preview_once(tmp_path: Path) -> None:
    harness = SearchHarness(tmp_path)
    try:
        result = Mailbox(harness.connection, harness.http).search('Kettle refund')
        assert len(result.items) == 1
        assert '-in:drafts' in harness.query
        assert harness.metadata_reads == 1
    finally:
        harness.http.close()
