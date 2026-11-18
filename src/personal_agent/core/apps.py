import atexit

from django.apps import AppConfig

from personal_agent.core.tracing import tracing


class CoreConfig(AppConfig):
    name = 'personal_agent.core'

    def ready(self) -> None:
        tracing.configure()
        atexit.register(tracing.shutdown)
