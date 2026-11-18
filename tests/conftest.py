import os

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')

# Controlled tests never export synthetic test spans to the live project.
os.environ['LANGFUSE_PUBLIC_KEY'] = ''
os.environ['LANGFUSE_SECRET_KEY'] = ''
