"""Explicit boundaries between pasted company text and the owner's request."""

from dataclasses import dataclass
import re


@dataclass(frozen=True)
class ParsedCompanyReply:
    prefix: str
    body: str | None
    owner_request: str

    def requests_followup(self) -> bool:
        return re.search(r'\b(?:draft|prepare|write)\b.*\b(?:follow[- ]?up|reply|response)\b',
                         self.owner_request, re.IGNORECASE) is not None


class CompanyReplyParser:
    def parse(self, message: str) -> ParsedCompanyReply | None:
        match = re.match(r'^(company reply|they replied|the company replied):\s*(.*)',
                         message.strip(), re.IGNORECASE | re.DOTALL)
        if not match:
            return None
        prefix, tail = match.group(1), match.group(2).strip()
        if not tail.startswith(('"', '“')):
            return ParsedCompanyReply(prefix, tail or None, '')
        opening = tail[0]
        closing = '”' if opening == '“' else '"'
        end = tail.rfind(closing)
        # The final outer delimiter retains paired quotations inside the reply.
        # An unpaired delimiter or an ambiguous quoted owner suffix requires clarification.
        if end <= 1 or (opening == '"' and tail.count('"') % 2):
            return ParsedCompanyReply(prefix, None, '')
        body = tail[1:end]
        request = tail[end + 1:].strip().lstrip('.!').strip()
        if request and not re.match(r'^(?:please\s+)?(?:save|draft|write|prepare|record)\b', request, re.IGNORECASE):
            return ParsedCompanyReply(prefix, None, '')
        return ParsedCompanyReply(prefix, body, request)
