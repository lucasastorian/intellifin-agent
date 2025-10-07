from __future__ import annotations

import re
from collections import defaultdict
from typing import List, Dict, Union
from bs4 import BeautifulSoup
from bs4.element import NavigableString, Tag

from pipeline.parsers.table_parser import TableParser

BLOCK_TAGS = {"div", "p", "h1", "h2", "h3", "h4", "h5", "h6", "table", "br", "hr", "ul", "ol", "li"}
BOLD_TAGS = {"b", "strong"}
ITALIC_TAGS = {"i", "em"}

_ws = re.compile(r"\s+")
_css_decl = re.compile(r"^[a-zA-Z\-]+\s*:\s*[^;]+;\s*$")


class Parser:
    """Base parser for document formatting."""

    def __init__(self, content: str):
        self.soup = BeautifulSoup(content, "lxml")
        self.includes_table = False
        self.pages: Dict[int, List[str]] = defaultdict(list)

    @staticmethod
    def _is_bold(el: Tag) -> bool:
        if not isinstance(el, Tag):
            return False
        style = (el.get("style") or "").lower()
        return (
            "font-weight:700" in style
            or "font-weight:bold" in style
            or el.name in BOLD_TAGS
        )

    @staticmethod
    def _is_italic(el: Tag) -> bool:
        if not isinstance(el, Tag):
            return False
        style = (el.get("style") or "").lower()
        return (
            "font-style:italic" in style
            or el.name in ITALIC_TAGS
        )

    @staticmethod
    def _is_block(el: Tag) -> bool:
        return isinstance(el, Tag) and el.name in BLOCK_TAGS

    @staticmethod
    def _has_break_before(el: Tag) -> bool:
        if not isinstance(el, Tag):
            return False
        style = (el.get("style") or "").lower().replace(" ", "")
        return (
            "page-break-before:always" in style
            or "break-before:page" in style
            or "break-before:always" in style
        )

    @staticmethod
    def _has_break_after(el: Tag) -> bool:
        if not isinstance(el, Tag):
            return False
        style = (el.get("style") or "").lower().replace(" ", "")
        return (
            "page-break-after:always" in style
            or "break-after:page" in style
            or "break-after:always" in style
        )

    @staticmethod
    def _is_hidden(el: Tag) -> bool:
        """Check if element has display:none"""
        if not isinstance(el, Tag):
            return False
        style = (el.get("style") or "").lower().replace(" ", "")
        return "display:none" in style

    @staticmethod
    def _clean_text(text: str) -> str:
        # Remove zero-width spaces, BOM, normalize NBSP
        text = text.replace("\u200b", "").replace("\ufeff", "").replace("\xa0", " ")
        return _ws.sub(" ", text).strip()

    @staticmethod
    def _wrap_markdown(el: Tag) -> str:
        """Return the prefix/suffix markdown wrapper for this element."""
        bold = Parser._is_bold(el)
        italic = Parser._is_italic(el)
        if bold and italic:
            return "***"
        if bold:
            return "**"
        if italic:
            return "*"
        return ""

    def _append(self, page_num: int, s: str) -> None:
        if s:
            self.pages[page_num].append(s)

    def _blankline_before(self, page_num: int) -> None:
        """Ensure exactly one blank line before the next block."""
        buf = self.pages[page_num]
        if not buf:
            return
        # Normalize to a single blank line
        if not buf[-1].endswith("\n"):
            buf.append("\n")
        if len(buf) >= 2 and buf[-1] == "\n" and buf[-2] == "\n":
            return
        buf.append("\n")

    def _blankline_after(self, page_num: int) -> None:
        """Mirror `_blankline_before` for symmetry; same rule."""
        self._blankline_before(page_num)

    def _process_text_node(self, node: NavigableString) -> str:
        text = self._clean_text(str(node))
        # Filter out CSS fragments like "blacklining:none;"
        if text and _css_decl.match(text):
            return ""
        return text

    def _process_element(self, element: Union[Tag, NavigableString]) -> str:
        if isinstance(element, NavigableString):
            return self._process_text_node(element)

        if element.name == "table":
            self.includes_table = True
            return TableParser(element).md().strip()

        # Handle lists
        if element.name in {"ul", "ol"}:
            items = []
            for li in element.find_all("li", recursive=False):
                item_text = self._process_element(li).strip()
                if item_text:
                    # Remove leading bullets/numbers from raw HTML
                    item_text = item_text.lstrip("•·∙◦▪▫-").strip()
                    items.append(item_text)
            if not items:
                return ""
            if element.name == "ol":
                return "\n".join(f"{i+1}. {t}" for i, t in enumerate(items))
            else:
                return "\n".join(f"- {t}" for t in items)

        if element.name == "li":
            # Render children inline for list items
            parts = [self._process_element(c) for c in element.children]
            return " ".join(p for p in parts if p).strip()

        parts: List[str] = []
        for child in element.children:
            if isinstance(child, NavigableString):
                t = self._process_text_node(child)
                if t:
                    parts.append(t)
            else:
                t = self._process_element(child)
                if t:
                    parts.append(t)

        text = " ".join(p for p in parts if p).strip()
        if not text:
            return ""

        wrap = self._wrap_markdown(element)
        return f"{wrap}{text}{wrap}" if wrap else text

    def _stream_pages(self, root: Union[Tag, NavigableString], page_num: int = 1) -> int:
        """Walk the DOM once; split only on CSS break styles."""
        if isinstance(root, Tag) and self._has_break_before(root):
            page_num += 1

        if isinstance(root, NavigableString):
            t = self._process_text_node(root)
            if t:
                self._append(page_num, t + " ")  # Add space to prevent wrapper bleeding
            return page_num

        if not isinstance(root, Tag):
            return page_num

        if self._is_hidden(root):
            return page_num

        is_block = self._is_block(root) and root.name not in {"br", "hr"}
        if is_block:
            self._blankline_before(page_num)

        # Handle tables and lists atomically
        if root.name in {"table", "ul", "ol"}:
            t = self._process_element(root)
            if t:
                self._append(page_num, t)
            self._blankline_after(page_num)
            if self._has_break_after(root):
                page_num += 1
            return page_num

        # For inline wrappers (bold/italic), render atomically to avoid wrapper bleeding
        wrap = self._wrap_markdown(root)
        if wrap and not is_block:
            t = self._process_element(root)
            if t:
                self._append(page_num, t + " ")
            if self._has_break_after(root):
                page_num += 1
            return page_num

        # Stream children for block elements
        current = page_num
        for child in root.children:
            current = self._stream_pages(child, current)

        if is_block:
            self._blankline_after(current)

        if self._has_break_after(root):
            current += 1

        return current

    def get_pages(self) -> List[dict]:
        self.pages = defaultdict(list)
        self.includes_table = False
        root = self.soup.body if self.soup.body else self.soup
        self._stream_pages(root, page_num=1)

        result: List[dict] = []
        for page_num in sorted(self.pages.keys()):
            raw = "".join(self.pages[page_num])

            # Collapse excessive newlines
            raw = re.sub(r"\n{3,}", "\n\n", raw)

            lines: List[str] = []
            for line in raw.split("\n"):
                line = line.strip()
                if line or (lines and lines[-1]):
                    lines.append(line)
            content = "\n".join(lines).strip()

            result.append({"page": page_num, "content": content})
        return result

    def markdown(self) -> str:
        pages = self.get_pages()
        return "\n\n".join(page["content"] for page in pages if page["content"])
