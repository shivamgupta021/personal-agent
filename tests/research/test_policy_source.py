import asyncio
import json
from typing import Any

import httpx2
import pytest

from research import constants
from research.tools.firecrawl import Firecrawl


def inspect(data: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    request_body: dict[str, Any] = {}
    def web(request: httpx2.Request) -> httpx2.Response:
        request_body.update(json.loads(request.content))
        return httpx2.Response(200, json={'success': True, 'data': data})
    async def run() -> dict[str, Any]:
        async with httpx2.AsyncClient(base_url='http://firecrawl', transport=httpx2.MockTransport(web)) as client:
            return await Firecrawl(client).inspect_page('https://official.example/start')
    return asyncio.run(run()), request_body


def test_inspection_preserves_backend_destination_and_bounded_observed_links() -> None:
    result, body = inspect({'markdown': 'Policy qualification.', 'links': ['/policy', 'https://support.example/contact',
        'javascript:alert(1)', 'http://127.0.0.1/private', '/policy'], 'metadata': {
        'url': 'https://official.example/final', 'sourceURL': 'https://official.example/start',
        'ogUrl': 'https://impostor.example/', 'statusCode': 200, 'contentType': 'text/html', 'finalUrlVerified': True}})
    assert body == {'url': 'https://official.example/start', 'formats': ['markdown', 'links'],
                    'onlyMainContent': False, 'maxAge': 0}
    assert result['requested_url'] == 'https://official.example/start'
    assert result['final_url'] == 'https://official.example/final'
    assert result['source_url'] == 'https://official.example/start'
    assert result['final_url_verified'] is True
    assert result['links'] == ['https://official.example/policy', 'https://support.example/contact']
    assert result['content'] == 'Policy qualification.'
    assert 'impostor' not in str(result)
    assert result['status_code'] == 200 and result['content_type'] == 'text/html'


@pytest.mark.parametrize('metadata', [
    {'sourceURL': 'https://official.example/start', 'ogUrl': 'https://official.example/start'},
    {'url': 'https://official.example/start'},
    {'url': 'https://official.example/start', 'finalUrlVerified': 'true'},
    {'url': 'http://127.0.0.1/private', 'finalUrlVerified': True},
])
def test_missing_or_unproven_final_url_cannot_become_verified(metadata: dict[str, Any]) -> None:
    result, _ = inspect({'markdown': 'Unverified content', 'links': [], 'metadata': {'statusCode': 200, **metadata}})
    assert result['final_url_verified'] is False
    assert result.get('final_url') != 'http://127.0.0.1/private'


@pytest.mark.parametrize('status', [None, True, '200', 302, 404])
def test_inspection_requires_actual_successful_target_status(status: object) -> None:
    result, _ = inspect({'markdown': 'Error page is not evidence', 'links': [], 'metadata': {
        'url': 'https://official.example/start', 'statusCode': status, 'finalUrlVerified': True}})
    assert result.get('error') == 'page_content_unavailable'


def test_content_and_link_truncation_are_explicit() -> None:
    result, _ = inspect({'markdown': 'x' * (constants.SOURCE_CONTENT_CHARACTERS + 1),
        'links': [f'https://official.example/{i}' for i in range(constants.SOURCE_MAX_LINKS + 1)],
        'metadata': {'url': 'https://official.example/start', 'statusCode': 200, 'finalUrlVerified': True}})
    assert len(result['content']) == constants.SOURCE_CONTENT_CHARACTERS
    assert result['truncated'] is True
    assert len(result['links']) == constants.SOURCE_MAX_LINKS
    assert result['links_truncated'] is True


def test_malformed_observed_link_does_not_abort_source_inspection() -> None:
    result, _ = inspect({'markdown': 'Policy.', 'links': ['http://[broken', 'https://official.example/policy'],
        'metadata': {'url': 'https://official.example/', 'statusCode': 200, 'finalUrlVerified': False}})
    assert result['links'] == ['https://official.example/policy']
    assert result['final_url_verified'] is False
