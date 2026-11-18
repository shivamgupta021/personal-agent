# Conservative serialized UTF-8 budget, not an exact provider token count.
# Count bytes as a deliberately pessimistic token estimate plus framing headroom.
MAX_REQUEST_BYTES = 48_000
REQUEST_HEADROOM = 8_000
MAX_OUTPUT_TOKENS = 4_000

DELETE_CONVERSATION_PHRASES = frozenset({
    'delete this entire conversation', 'forget this entire conversation',
    'delete all conversation history', 'forget all conversation history',
})
