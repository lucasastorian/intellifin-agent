import logging


def suppress_edgar_no_xbrl_warnings() -> None:
    """
    Suppress noisy EDGAR warnings like:
      "No XBRL attachments found in filing ..."

    This keeps logs clean during eval runs without muting all edgar.core logs.
    """

    class _NoXbrlFilter(logging.Filter):
        def filter(self, record: logging.LogRecord) -> bool:
            try:
                return "No XBRL attachments found in filing" not in record.getMessage()
            except Exception:
                return True

    logging.getLogger("edgar.core").addFilter(_NoXbrlFilter())

