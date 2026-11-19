MAX_INPUT_CHARS = 16000
MAX_CONTEXT_CHARS = 32000
MAX_FACT_CHARS = 2000
MAX_MODEL_REQUESTS = 3
MAX_OUTPUT_TOKENS = 3000
RUN_TIMEOUT_SECONDS = 120
MATERIAL_FIELDS = ('company', 'purchase_context', 'issue', 'requested_remedy', 'purchase_date', 'amount_paid')

VALIDATION_RETRIES = 1

DRAFT_REQUIRED_FIELDS = (*MATERIAL_FIELDS, 'refund_amount')
DRAFT_FACT_FIELDS = ('company', 'purchase_context', 'purchase_date', 'delivery_date',
                     'order_reference', 'issue', 'amount_paid', 'refund_amount', 'currency', 'evidence')
DRAFT_MATERIAL_FIELDS = (*DRAFT_FACT_FIELDS, 'requested_remedy')

POLICY_MAX_PAGES = 6
POLICY_MAX_FINDINGS = 8
POLICY_MAX_QUOTE_CHARS = 2000
POLICY_PUBLIC_FIELD_CHARS = 80

POLICY_CONTEXT_CHARS = 128000

POLICY_SITE_URL_CHARS = 300
