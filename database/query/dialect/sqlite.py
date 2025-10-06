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

    def bm25(self, fts_table: str, weights: tuple = None) -> str:
        """Return BM25 ranking expression for FTS table.

        Args:
            fts_table: Name of the FTS virtual table
            weights: Optional per-column weights (e.g., (0.2, 1.0) to down-weight first column)
        """
        if weights:
            args = ", ".join(str(w) for w in weights)
            return f"bm25({self.q(fts_table)}, {args})"
        return f"bm25({self.q(fts_table)})"

    def like_ci(self) -> str:
        """Return case-insensitive LIKE collation."""
        return "COLLATE NOCASE"
