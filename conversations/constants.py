# Conservative serialized UTF-8 budget, not an exact provider token count.
# Count bytes as a deliberately pessimistic token estimate plus framing headroom.
MAX_REQUEST_BYTES = 48_000
REQUEST_HEADROOM = 8_000
MAX_OUTPUT_TOKENS = 4_000

DELETE_CONVERSATION_PHRASES = frozenset({
    'delete this entire conversation', 'forget this entire conversation',
    'delete all conversation history', 'forget all conversation history',
})

COMPACTION_TRIGGER_BYTES = 32_000
RECENT_CONTEXT_BYTES = 6_000
SUMMARY_OUTPUT_TOKENS = 1_500
SUMMARY_TIMEOUT_SECONDS = 45
SUMMARY_RETRIES = 0
MAX_COMPACTIONS_PER_TURN = 2
