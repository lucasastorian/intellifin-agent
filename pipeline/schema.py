from database.schema import Schema, Table
from database.schema.fields import Serial, Text, Integer, Boolean, JSONField, Date, Timestamp, Enum


class Companies(Table):
    __tablename__ = "companies"

    id = Serial()
    name = Text(nullable=False)
    symbols = JSONField(nullable=False, index=True)
    exchanges = JSONField(nullable=True, index=True)
    cik = Text(nullable=False, unique=True, index=True)
    sic = Text(nullable=False, index=True)
    industry = Text(nullable=True)
    fiscal_year_end = Text(nullable=True)
    created_at = Timestamp(nullable=False, default="CURRENT_TIMESTAMP")
    updated_at = Timestamp(nullable=False, default="CURRENT_TIMESTAMP", auto_update=True)


class Filings(Table):
    __tablename__ = "filings"

    id = Serial()
    form = Enum(choices=['10-K', '10-Q', '8-K', 'DEF 14A', '6-K', '20-F', '10-K/A', '10-Q/A', '8-K/A', 'DEF 14A/A',
                         '6-K/A', '20-F/A'], nullable=False, index=True)
    amendment = Boolean(default=False, nullable=False)
    items = JSONField(nullable=True)  # For 8-Ks: list of items like ["2.02", "9.01"]
    press_release = Boolean(default=False, nullable=False)
    fiscal_year = Integer(nullable=True, index=True)
    fiscal_period = Enum(choices=['Q1', 'Q2', 'Q3', 'Q4', 'FY'],  nullable=True)  # NOTE: 'Q4' isn't filed with SEC.
    filing_date = Date(nullable=False)
    report_date = Date(nullable=True)
    accession_number = Text(nullable=False, unique=True, index=True)

    company_id = Integer(nullable=False, foreign_key="companies.id", on_delete="CASCADE", index=True)

    created_at = Timestamp(nullable=False, default="CURRENT_TIMESTAMP")
    updated_at = Timestamp(nullable=False, default="CURRENT_TIMESTAMP", auto_update=True)


class FinancialStatements(Table):
    __tablename__ = "financial_statements"

    id = Serial()

    statement_type = Enum(choices=['income_statement', 'balance_sheet', 'cash_flow', 'equity_statement',
                                   'comprehensive_income'], nullable=False, index=True)

    content = Text(nullable=False)  # Raw text if needed
    sections = JSONField(nullable=False)  # Structured sections as JSON (NULL if not present)

    report_date = Date(nullable=False, index=True)
    fiscal_year = Integer(nullable=False, index=True)
    fiscal_period = Enum(choices=['Q1', 'Q2', 'Q3', 'Q4', 'FY'], nullable=False, index=True)
    inferred = Boolean(nullable=False, default=False)
    edgar_link = Text(nullable=True)

    filing_id = Integer(nullable=False, foreign_key="filings.id", on_delete="CASCADE", index=True)
    company_id = Integer(nullable=False, foreign_key="companies.id", on_delete="CASCADE", index=True)

    created_at = Timestamp(nullable=False, default="CURRENT_TIMESTAMP")
    updated_at = Timestamp(nullable=False, default="CURRENT_TIMESTAMP", auto_update=True)


class FilingNotes(Table):
    __tablename__ = "filing_notes"

    id = Serial()

    title = Text(nullable=False)
    filename = Text(nullable=False)
    content = Text(nullable=False)

    filing_id = Integer(nullable=False, foreign_key="filings.id", on_delete="CASCADE", index=True)
    company_id = Integer(nullable=False, foreign_key="companies.id", on_delete="CASCADE", index=True)

    created_at = Timestamp(nullable=False, default="CURRENT_TIMESTAMP")
    updated_at = Timestamp(nullable=False, default="CURRENT_TIMESTAMP", auto_update=True)

    __uniques__ = [("filing_id", "filename")]


class FilingPages(Table):
    __tablename__ = "filing_pages"

    id = Serial()
    page = Integer(nullable=False, index=True)
    content = Text(nullable=False)

    filing_id = Integer(nullable=False, foreign_key="filings.id", on_delete="CASCADE", index=True)
    company_id = Integer(nullable=False, foreign_key="companies.id", on_delete="CASCADE", index=True)

    created_at = Timestamp(nullable=False, default="CURRENT_TIMESTAMP")
    updated_at = Timestamp(nullable=False, default="CURRENT_TIMESTAMP", auto_update=True)

    __uniques__ = [("filing_id", "page")]


class PressReleasePages(Table):
    __tablename__ = "press_release_pages"

    id = Serial()
    page = Integer(nullable=False, index=True)
    content = Text(nullable=False)

    filing_id = Integer(nullable=False, foreign_key="filings.id", on_delete="CASCADE", index=True)
    company_id = Integer(nullable=False, foreign_key="companies.id", on_delete="CASCADE", index=True)

    created_at = Timestamp(nullable=False, default="CURRENT_TIMESTAMP")
    updated_at = Timestamp(nullable=False, default="CURRENT_TIMESTAMP", auto_update=True)

    __uniques__ = [("filing_id", "page")]


schema = Schema()
schema.add_table(Companies)
schema.add_table(Filings)
schema.add_table(FinancialStatements)
schema.add_table(FilingNotes)
schema.add_table(FilingPages)
schema.add_table(PressReleasePages)
