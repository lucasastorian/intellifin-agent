from database.schema import Schema, Table
from database.schema.fields import Serial, Text, Integer, Boolean, JSONField, Date, Timestamp, Enum, Float
from database.schema.view import View, Field


class Companies(Table):
    __tablename__ = "companies"

    id = Serial()
    name = Text(nullable=False)
    symbols = JSONField(nullable=False, index=True)
    exchanges = JSONField(nullable=True, index=True)
    cik = Text(nullable=False, unique=True, index=True)
    sic = Text(nullable=True, index=False)
    sector = Text(nullable=True)
    industry = Text(nullable=True)
    market_cap = Float(nullable=True)
    fiscal_year_end = Text(nullable=True)
    synced = Boolean(default=False)
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
    fiscal_period = Enum(choices=['Q1', 'Q2', 'Q3', 'Q4', 'FY'], nullable=True)  # NOTE: 'Q4' isn't filed with SEC.
    filing_date = Date(nullable=False)
    report_date = Date(nullable=True)
    accession_number = Text(nullable=False, unique=True, index=True)

    company_id = Integer(nullable=False, foreign_key="companies.id", on_delete="CASCADE", index=True)

    created_at = Timestamp(nullable=False, default="CURRENT_TIMESTAMP")
    updated_at = Timestamp(nullable=False, default="CURRENT_TIMESTAMP", auto_update=True)


class CompanyFilings(View):
    __viewname__ = "company_filings"
    __tables__ = (Filings, Companies)

    id = Field(table="filings", field="id")
    form = Field(table="filings", field="form")
    amendment = Field(table="filings", field="amendment")
    items = Field(table="filings", field="items")
    press_release = Field(table="filings", field="press_release")
    fiscal_year = Field(table="filings", field="fiscal_year")
    fiscal_period = Field(table="filings", field="fiscal_period")
    filing_date = Field(table="filings", field="filing_date")
    report_date = Field(table="filings", field="report_date")
    accession_number = Field(table="filings", field="accession_number")

    company_id = Field(table="filings", field="company_id")
    company_name = Field(table="companies", field="name")
    company_symbols = Field(table="companies", field="symbols")
    company_exchanges = Field(table="companies", field="exchanges")
    company_sector = Field(table="companies", field="sector")
    company_industry = Field(table="companies", field="industry")

    created_at = Field(table="filings", field="created_at")
    updated_at = Field(table="filings", field="updated_at")


class FinancialStatements(Table):
    __tablename__ = "financial_statements"

    id = Serial()

    type = Enum(choices=['income_statement', 'balance_sheet', 'cash_flow', 'equity_statement',
                         'comprehensive_income'], nullable=False, index=True)

    data = JSONField(nullable=False)  # Structured sections as JSON (NULL if not present)

    filing_id = Integer(nullable=False, foreign_key="filings.id", on_delete="CASCADE", index=True)
    company_id = Integer(nullable=False, foreign_key="companies.id", on_delete="CASCADE", index=True)

    created_at = Timestamp(nullable=False, default="CURRENT_TIMESTAMP")
    updated_at = Timestamp(nullable=False, default="CURRENT_TIMESTAMP", auto_update=True)

    __uniques__ = [("filing_id", "type")]


class FilingNotes(Table):
    __tablename__ = "filing_notes"

    id = Serial()

    title = Text(nullable=False)
    filename = Text(nullable=False)
    content = Text(nullable=False, fts=True)

    filing_id = Integer(nullable=False, foreign_key="filings.id", on_delete="CASCADE", index=True)
    company_id = Integer(nullable=False, foreign_key="companies.id", on_delete="CASCADE", index=True)

    created_at = Timestamp(nullable=False, default="CURRENT_TIMESTAMP")
    updated_at = Timestamp(nullable=False, default="CURRENT_TIMESTAMP", auto_update=True)

    __uniques__ = [("filing_id", "filename")]


class FilingPages(Table):
    __tablename__ = "filing_pages"

    id = Serial()
    page = Integer(nullable=False, index=True)
    content = Text(nullable=False, fts=True)

    filing_id = Integer(nullable=False, foreign_key="filings.id", on_delete="CASCADE", index=True)
    company_id = Integer(nullable=False, foreign_key="companies.id", on_delete="CASCADE", index=True)

    created_at = Timestamp(nullable=False, default="CURRENT_TIMESTAMP")
    updated_at = Timestamp(nullable=False, default="CURRENT_TIMESTAMP", auto_update=True)

    __uniques__ = [("filing_id", "page")]


class CompanyFilingPages(View):
    __viewname__ = "company_filing_pages"
    __tables__ = (FilingPages, Filings, Companies)

    id = Field(table="filing_pages", field="id")
    page = Field(table="filing_pages", field="page")
    content = Field(table="filing_pages", field="content")

    filing_id = Field(table="filing_pages", field="filing_id")
    form = Field(table="filings", field="form")
    amendment = Field(table="filings", field="amendment")
    items = Field(table="filings", field="items")
    press_release = Field(table="filings", field="press_release")
    fiscal_year = Field(table="filings", field="fiscal_year")
    fiscal_period = Field(table="filings", field="fiscal_period")
    filing_date = Field(table="filings", field="filing_date")
    report_date = Field(table="filings", field="report_date")
    accession_number = Field(table="filings", field="accession_number")

    company_id = Field(table="filing_pages", field="company_id")
    company_name = Field(table="companies", field="name")
    company_symbols = Field(table="companies", field="symbols")
    company_exchanges = Field(table="companies", field="exchanges")
    company_sector = Field(table="companies", field="sector")
    company_industry = Field(table="companies", field="industry")


class CompanyFilingNotes(View):
    __viewname__ = "company_filing_notes"
    __tables__ = (FilingNotes, Filings, Companies)

    id = Field(table="filing_notes", field="id")
    title = Field(table="filing_notes", field="title")
    filename = Field(table="filing_notes", field="filename")
    content = Field(table="filing_notes", field="content")

    filing_id = Field(table="filing_notes", field="filing_id")
    form = Field(table="filings", field="form")
    amendment = Field(table="filings", field="amendment")
    items = Field(table="filings", field="items")
    press_release = Field(table="filings", field="press_release")
    fiscal_year = Field(table="filings", field="fiscal_year")
    fiscal_period = Field(table="filings", field="fiscal_period")
    filing_date = Field(table="filings", field="filing_date")
    report_date = Field(table="filings", field="report_date")
    accession_number = Field(table="filings", field="accession_number")

    company_id = Field(table="filing_notes", field="company_id")
    company_name = Field(table="companies", field="name")
    company_symbols = Field(table="companies", field="symbols")
    company_exchanges = Field(table="companies", field="exchanges")
    company_sector = Field(table="companies", field="sector")
    company_industry = Field(table="companies", field="industry")


class PressReleasePages(Table):
    __tablename__ = "press_release_pages"

    id = Serial()
    page = Integer(nullable=False, index=True)
    content = Text(nullable=False, fts=True)

    filing_id = Integer(nullable=False, foreign_key="filings.id", on_delete="CASCADE", index=True)
    company_id = Integer(nullable=False, foreign_key="companies.id", on_delete="CASCADE", index=True)

    created_at = Timestamp(nullable=False, default="CURRENT_TIMESTAMP")
    updated_at = Timestamp(nullable=False, default="CURRENT_TIMESTAMP", auto_update=True)

    __uniques__ = [("filing_id", "page")]


class CompanyFilingPressReleases(View):
    __viewname__ = "company_filing_press_releases"
    __tables__ = (PressReleasePages, Filings, Companies)

    id = Field(table="press_release_pages", field="id")
    page = Field(table="press_release_pages", field="page")
    content = Field(table="press_release_pages", field="content")

    filing_id = Field(table="press_release_pages", field="filing_id")
    form = Field(table="filings", field="form")
    amendment = Field(table="filings", field="amendment")
    items = Field(table="filings", field="items")
    press_release = Field(table="filings", field="press_release")
    fiscal_year = Field(table="filings", field="fiscal_year")
    fiscal_period = Field(table="filings", field="fiscal_period")
    filing_date = Field(table="filings", field="filing_date")
    report_date = Field(table="filings", field="report_date")
    accession_number = Field(table="filings", field="accession_number")

    company_id = Field(table="press_release_pages", field="company_id")
    company_name = Field(table="companies", field="name")
    company_symbols = Field(table="companies", field="symbols")
    company_exchanges = Field(table="companies", field="exchanges")
    company_sector = Field(table="companies", field="sector")
    company_industry = Field(table="companies", field="industry")


schema = Schema()
schema.add_table(Companies)
schema.add_table(Filings)
schema.add_table(FinancialStatements)
schema.add_table(FilingNotes)
schema.add_table(FilingPages)
schema.add_table(PressReleasePages)

schema.add_view(CompanyFilings)
schema.add_view(CompanyFilingPages)
schema.add_view(CompanyFilingNotes)
schema.add_view(CompanyFilingPressReleases)
