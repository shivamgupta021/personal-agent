"""Owner-only conversation requests, serialized by the polling loop."""

import asyncio
import logging
from collections.abc import Awaitable, Callable

from core.errors import user_error_message
from attachments.records import Upload
from core.turns import OwnerTurn, PreparedReply

from telegram_bot.client import TelegramClient, TelegramError
from telegram_bot.state import PollingState

logger = logging.getLogger(__name__)


class TelegramService:
    def __init__(self, client: TelegramClient, state: PollingState, owner_id: int,
                 turn_handler: Callable[[OwnerTurn], Awaitable[str | PreparedReply]], *, identity: str) -> None:
        self.client = client
        self.state = state
        self.owner_id = owner_id
        self.turn_handler = turn_handler
        self.identity = identity
        if not identity:
            raise ValueError('Normalized turns require the bot/owner identity')

    async def poll_once(self) -> None:
        offset = self.state.offset()
        for update in await self.client.updates(offset):
            update_id = update['update_id']
            if update_id < offset:
                continue
            # Persist before any work: a crash or uncertain send never replays research.
            self.state.advance(update_id + 1)
            offset = update_id + 1
            callback = update.get('callback_query', {})
            message = callback.get('message', {}) if callback else update.get('message', {})
            chat = message.get('chat', {})
            if ((callback.get('from', {}) if callback else message.get('from', {})).get('id') != self.owner_id
                    or chat.get('type') != 'private' or chat.get('id') != self.owner_id):
                continue
            if callback:
                await self.client.request('answerCallbackQuery', {'callback_query_id': callback['id']})
            question = (next((button.get('text', 'Button selected')
                for row in message.get('reply_markup', {}).get('inline_keyboard', [])
                for button in row if button.get('callback_data') == callback.get('data')), 'Button selected')
                if callback else message.get('text') or message.get('caption'))
            file = message.get('document')
            photos = message.get('photo', [])
            if not file and photos:
                file = {**photos[-1], 'file_name': 'photo.jpg', 'mime_type': 'image/jpeg'}
            if not isinstance(question, str) or not question.strip():
                if not file:
                    continue
                question = 'Here is my receipt or supporting document. Add it to the current case and tell me the key details.'
            if question.startswith('/'):
                await self.client.send(self.owner_id,
                    'Create a named grievance case, list or select a case, or send a public research question. '
                    'Case facts and selection persist; conversation history survives restarts. Send a follow-up or explicitly return to general research to leave a case. '
                    'If no reply arrives, send the request again. Cases stay private in traces; public research debugging may capture content.')
                continue
            logger.info('Owner request accepted')
            try:
                uploads: tuple[Upload, ...] = ()
                if file:
                    uploads = (await self.client.download(file['file_id'], file.get('file_name', 'attachment'),
                        file.get('mime_type', 'application/octet-stream'), file.get('file_size', 0)),)
                answer = await self.turn_handler(OwnerTurn(self.identity, self.identity,
                    f'telegram:{update_id}', question, uploads, callback.get('data', '')))
            except Exception as error:
                logger.warning('Request failed; request will not be replayed')
                answer = (str(error) if isinstance(error, TelegramError) else user_error_message(error)) + ' Please send your question again.'
            await self.client.send(self.owner_id, answer)
            logger.info('Reply delivered')

    async def run(self) -> None:
        with self.state.locked():
            logger.info('Telegram polling started; interrupted requests require manual resend')
            while True:
                try:
                    await self.poll_once()
                except TelegramError as error:
                    if not error.retryable:
                        logger.error('Telegram configuration failed; check token, permissions, webhook and other pollers')
                        raise
                    logger.warning('Telegram unavailable; no automatic reply retries')
                    await asyncio.sleep(error.retry_after)
