MAX_PREFERENCES = 30
KEY_CHARS = 80
VALUE_CHARS = 300
SOURCE_CHARS = 2000
AUTHORIZATION_OUTPUT_TOKENS = 1500
AUTHORIZATION_TIMEOUT_SECONDS = 60

# Whole-message read shortcuts; unfamiliar or mixed requests use the model router.
INSPECTION_PHRASES = frozenset((
    'what preferences have you saved',
    'what are my saved preferences',
    'what are my preferences',
    'what preferences do you remember',
    'what do you remember about my preferences',
    'show my saved preferences',
    'show my preferences',
    'list my saved preferences',
    'list my preferences',
    'do you have any preferences saved',
    'have you saved any preferences',
))
