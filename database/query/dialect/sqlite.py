"""SQLite-specific SQL dialect."""


class SQLiteDialect:
    """Encapsulates SQLite-specific syntax and functions."""

    ident_quote = "`"

    def q(self, ident: str) -> str:
        """Quote an identifier."""
        return f"{self.ident_quote}{ident}{self.ident_quote}"

    def regexp_fn(self) -> str:
        """Return the REGEXP function name."""
        return "REGEXP"

    def bm25(self, fts_table: str) -> str:
        """Return BM25 ranking expression for FTS table."""
        return f"bm25({self.q(fts_table)})"

    def like_ci(self) -> str:
        """Return case-insensitive LIKE collation."""
        return "COLLATE NOCASE"
