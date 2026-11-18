import atexit

from django.apps import AppConfig

from core.tracing import tracing


class CoreConfig(AppConfig):
    name = 'core'

    def ready(self) -> None:
        tracing.configure()
        atexit.register(tracing.shutdown)
