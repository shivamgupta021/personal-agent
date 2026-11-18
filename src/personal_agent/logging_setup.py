"""Configure application progress once; leave third-party payload logging off."""

import logging


def configure_logging() -> None:
    logger = logging.getLogger('personal_agent')
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter('%(levelname)s %(name)s: %(message)s'))
        logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False
