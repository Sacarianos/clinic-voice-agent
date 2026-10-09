"""Logging setup for the voice server: everything logged, from any library, is masked before it is written."""

import logging
import sys
from typing import TextIO

from loguru import logger

from clinic_agent.phi import PHI


def configure_logging(level: str, sink: TextIO = sys.stderr) -> None:
    """Sends loguru's records (Pipecat logs through loguru) and the standard library's to `sink`, masked.

    Masking runs on the whole formatted line, exception traceback included. Tracebacks never show local
    variables' values, which can hold a Caller's words.
    """

    def write_masked(message) -> None:
        line = str(message)
        # A name a tool was called with is masked in every later line too.
        PHI.learn_keyed_values(line)
        sink.write(PHI.mask(line))

    logger.remove()
    logger.add(write_masked, level=level, diagnose=False, backtrace=False)
    logging.basicConfig(handlers=[_ToLoguru()], level=logging.getLevelNamesMapping().get(level, 0), force=True)


class _ToLoguru(logging.Handler):
    """Hands standard library records (httpx, uvicorn, OpenTelemetry) to loguru, so they are masked too."""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            level: str | int = logger.level(record.levelname).name
        except ValueError:
            level = record.levelno
        logger.patch(
            lambda r: r.update(name=record.name, function=record.funcName, line=record.lineno)
        ).opt(exception=record.exc_info).log(level, record.getMessage())
