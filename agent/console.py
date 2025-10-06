from typing import Optional, List
import os
import sys

try:
    from rich.console import Console
    from rich.table import Table
    from rich.panel import Panel
    from rich.rule import Rule
    from rich.text import Text
    _HAS_RICH = True

except Exception:
    _HAS_RICH = False


class _Ansi:
    RESET = "\033[0m"
    DIM = "\033[2m"
    BOLD = "\033[1m"
    RED = "\033[31m"
    GREEN = "\033[32m"
    CYAN = "\033[36m"
    YELLOW = "\033[33m"
    MAGENTA = "\033[35m"


def _supports_ansi() -> bool:
    return sys.stdout.isatty() and os.getenv("TERM") not in (None, "dumb")


class ConsoleLogger:
    def __init__(self):
        self.use_rich = _HAS_RICH and _supports_ansi()
        self.use_ansi = _supports_ansi()

        if self.use_rich:
            self.console = Console(highlight=False, soft_wrap=False)
        else:
            self.console = None

    # --- high-level events -------------------------------------------------

    def turn(self, n: int, max_iter: int):
        title = f"Turn {n}/{max_iter}"
        if self.use_rich:
            self.console.rule(title)
        else:
            line = f"{_Ansi.CYAN}{title}{_Ansi.RESET}" if self.use_ansi else title
            print(f"\n{'=' * 12} {line} {'=' * 12}")

    def tool_called(self, name: str, args_preview: str):
        head = f"Action: {name}"
        if self.use_rich:
            self.console.print(Panel.fit(args_preview, title=head, title_align="left", border_style="cyan"))
        else:
            head = f"{_Ansi.CYAN}{head}{_Ansi.RESET}" if self.use_ansi else head
            print(f"\n--- {head} ---\n{args_preview}")

    def tool_result_summary(self, name: str, summary: str):
        head = f"Result: {name}"
        if self.use_rich:
            self.console.print(Panel.fit(summary, title=head, title_align="left", border_style="green"))
        else:
            head = f"{_Ansi.GREEN}{head}{_Ansi.RESET}" if self.use_ansi else head
            print(f"\n+++ {head} +++\n{summary}")

    def warning(self, msg: str):
        if self.use_rich:
            self.console.print(Panel.fit(msg, border_style="yellow", title="Warning"))
        else:
            msg = f"{_Ansi.YELLOW}{msg}{_Ansi.RESET}" if self.use_ansi else msg
            print(f"\n[WARN] {msg}")

    def error(self, msg: str):
        if self.use_rich:
            self.console.print(Panel.fit(msg, border_style="red", title="Error"))
        else:
            msg = f"{_Ansi.RED}{msg}{_Ansi.RESET}" if self.use_ansi else msg
            print(f"\n[ERROR] {msg}")

    def summarize_search_filings_md(self, md: str, max_rows: int = 3) -> str:
        """
        Show header + separator + first N rows and a total-row count.
        Works on standard pandas DataFrame .to_markdown() output.
        """
        lines = [ln for ln in (md or "").splitlines() if ln.strip()]
        if len(lines) < 2:
            return (lines[0] if lines else "") + "\n(0 rows)"

        # Markdown table format: header, separator, data rows...
        header = lines[0]
        sep = lines[1] if len(lines) > 1 else ""
        data = lines[2:]

        shown = data[:max_rows]
        total = len(data)
        tail = f"\n… showing {len(shown)} of {total} row(s)"
        return "\n".join([header, sep, *shown]) + tail

    def summarize_read_filing_md(self, md: str, preview_chars: int = 600) -> str:
        """
        Extract the small header block, then show the first page's first ~600 chars.
        Assumes your ReadFiling formats the header first, then '---\\n**Page X**\\n\\n...'
        """
        if not md:
            return "(empty)"
        lines = md.splitlines()
        # Grab header until first blank line
        header_lines: List[str] = []
        i = 0
        while i < len(lines) and lines[i].strip():
            header_lines.append(lines[i])
            i += 1
        header = "\n".join(header_lines)

        page_start = md.find("\n---\n**Page ")
        preview = md[page_start + 1:] if page_start >= 0 else md
        preview = preview.strip().replace("\n\n", "\n")
        if len(preview) > preview_chars:
            preview = preview[:preview_chars].rstrip() + "…"

        return f"{header}\n\n{preview}"
