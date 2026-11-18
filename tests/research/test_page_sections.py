import asyncio

import httpx2

from research.tools.firecrawl import Firecrawl


def test_read_page_returns_anchor_definition_without_navigation_or_next_definition() -> None:
    markdown = ('Navigation ' * 2000 + '\n'
                '[timeout reference](https://example.com/api#timeout)\n\n'
                'timeout(delay)[¶](https://example.com/api#timeout "Link to this definition")\n\n'
                'The deadline raises TimeoutError.\n\n'
                'next()[¶](https://example.com/api#next "Link to this definition")\n\n'
                'Unrelated next definition.')

    def web(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, json={'success': True, 'data': {'markdown': markdown}})

    async def exercise() -> None:
        async with httpx2.AsyncClient(base_url='http://firecrawl', transport=httpx2.MockTransport(web)) as client:
            result = await Firecrawl(client).read_page('https://example.com/api#timeout')
        assert 'TimeoutError' in result['content']
        assert 'Navigation' not in result['content']
        assert 'Unrelated' not in result['content']
        assert result['next_offset'] is None

    asyncio.run(exercise())


def test_read_page_preserves_pagination_when_anchor_is_missing() -> None:
    markdown = 'Intro ' * 3000 + 'Later evidence.'

    def web(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, json={'success': True, 'data': {'markdown': markdown}})

    async def exercise() -> None:
        async with httpx2.AsyncClient(base_url='http://firecrawl', transport=httpx2.MockTransport(web)) as client:
            tool = Firecrawl(client)
            first = await tool.read_page('https://example.com/api#missing')
            second = await tool.read_page('https://example.com/api#missing', offset=first['next_offset'])
        assert first['section_found'] is False
        assert first['offset_scope'] == 'page'
        assert first['content'] + second['content'] == markdown

    asyncio.run(exercise())
