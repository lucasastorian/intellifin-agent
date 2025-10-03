"""Database error hierarchy for clean exception handling"""


class DatabaseError(Exception):
    """Base exception for all database errors"""
    pass


class ConstraintError(DatabaseError):
    """Base exception for constraint violations"""
    pass


class ForeignKeyError(ConstraintError):
    """Foreign key constraint violation"""
    pass


class UniqueConstraintError(ConstraintError):
    """Unique constraint violation"""
    pass


class NotNullViolation(ConstraintError):
    """NOT NULL constraint violation"""
    pass


class CheckConstraintError(ConstraintError):
    """CHECK constraint violation (e.g., enum values)"""
    pass
