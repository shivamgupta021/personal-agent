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

RECALL_QUERY_CHARS = 200
RECALL_MAX_RESULTS = 5
RECALL_EXCERPT_CHARS = 500
RECALL_CONTEXT_CHARS = 100
MAX_RECALL_CALLS = 3

FACT_EDIT_MAX_BATCHES = 3
FACT_EDIT_BATCH_CHARS = 16_000
FACT_EDIT_SCOPE_CHARS = 500
FACT_EDIT_OUTPUT_TOKENS = 2_000
FACT_EDIT_TIMEOUT_SECONDS = 90
FACT_EDIT_RECENT_BYTES = 12_000

# Bound routing context; larger histories use normal compaction/recall.
MAX_INLINE_CONTEXT_BYTES = 16_000
MAX_INLINE_REPLY_CHARS = 4_000
