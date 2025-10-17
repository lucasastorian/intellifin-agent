import logging


def suppress_edgar_no_xbrl_warnings() -> None:
    """
    Suppress noisy EDGAR warnings like:
      "No XBRL attachments found in filing ..."
      "is an amended filing and may not contain full XBRL data ..."

    This keeps logs clean during eval runs without muting all edgar.core logs.
    """

    class _NoXbrlFilter(logging.Filter):
        def filter(self, record: logging.LogRecord) -> bool:
            try:
                message = record.getMessage()
                return (
                    "No XBRL attachments found in filing" not in message
                    and "is an amended filing and may not contain full XBRL data" not in message
                )
            except Exception:
                return True

    logging.getLogger("edgar.core").addFilter(_NoXbrlFilter())


def suppress_pyrate_limiter_warnings() -> None:
    """Suppress pyrate_limiter warning: "async call made without an async bucket".

    We target only this specific noisy warning to avoid hiding useful logs.
    """

    class _AsyncBucketFilter(logging.Filter):
        def filter(self, record: logging.LogRecord) -> bool:
            try:
                return "async call made without an async bucket" not in record.getMessage()
            except Exception:
                return True

    logger = logging.getLogger("pyrate_limiter")
    logger.addFilter(_AsyncBucketFilter())
