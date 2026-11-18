"""Select bounded documentation sections using explicit Markdown permalinks."""

import re
from urllib.parse import unquote, urljoin, urlsplit


class MarkdownPage:
    # Definition/heading permalinks identify targets; ordinary references do not.
    _permalink = re.compile(
        r'\[¶\]\(([^\s)]+)(?:\s+"[^"]*")?\)',
    )

    def __init__(self, url: str, markdown: str) -> None:
        self.url = url
        self.markdown = markdown

    def section(self) -> tuple[str, bool]:
        target = urlsplit(self.url)
        if not target.fragment:
            return self.markdown, False
        matches = list(self._permalink.finditer(self.markdown))
        for index, match in enumerate(matches):
            link = urlsplit(urljoin(self.url, match.group(1)))
            if (link.scheme, link.netloc, link.path, link.query, unquote(link.fragment)) != (
                target.scheme, target.netloc, target.path, target.query, unquote(target.fragment)
            ):
                continue
            start = self.markdown.rfind('\n', 0, match.start()) + 1
            end = len(self.markdown)
            if index + 1 < len(matches):
                end = self.markdown.rfind('\n', 0, matches[index + 1].start()) + 1
            return self.markdown[start:end].strip(), True
        return self.markdown, False
