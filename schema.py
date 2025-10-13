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
    country = Text(nullable=True)
    market_cap = Float(nullable=True)
    fiscal_year_end = Text(nullable=True)
    delisted = Boolean(default=False, nullable=False)
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
    num_pages = Integer(nullable=True)
    num_attachments = Integer(nullable=True)
    title = Text(nullable=True, fts=True)  # LLM-generated title (for 8-K, 6-K)

    summary = Text(nullable=True, fts=True, vector=True)  # LLM-generated summary (context from header baked in)
    synced = Boolean(default=False, nullable=False, index=True)

    company_id = Integer(nullable=False, foreign_key="companies.id", on_delete="CASCADE", index=True)

    created_at = Timestamp(nullable=False, default="CURRENT_TIMESTAMP")
    updated_at = Timestamp(nullable=False, default="CURRENT_TIMESTAMP", auto_update=True)


class CompanyFilings(View):
    __viewname__ = "company_filings"
    __tables__ = (Filings, Companies)

    id = Field(table="filings", field="id")
    form = Field(table="filings", field="form")
    amendment = Field(table="filings", field="amendment")
    title = Field(table="filings", field="title")
    summary = Field(table="filings", field="summary")
    items = Field(table="filings", field="items")
    press_release = Field(table="filings", field="press_release")
    fiscal_year = Field(table="filings", field="fiscal_year")
    fiscal_period = Field(table="filings", field="fiscal_period")
    filing_date = Field(table="filings", field="filing_date")
    report_date = Field(table="filings", field="report_date")
    accession_number = Field(table="filings", field="accession_number")
    num_pages = Field(table="filings", field="num_pages")
    num_attachments = Field(table="filings", field="num_attachments")

    company_id = Field(table="filings", field="company_id")
    company_name = Field(table="companies", field="name")
    company_symbols = Field(table="companies", field="symbols")
    company_exchanges = Field(table="companies", field="exchanges")
    company_sector = Field(table="companies", field="sector")
    company_industry = Field(table="companies", field="industry")
    company_delisted = Field(table="companies", field="delisted")

    created_at = Field(table="filings", field="created_at")
    updated_at = Field(table="filings", field="updated_at")


class FinancialStatements(Table):
    __tablename__ = "financial_statements"

    id = Serial()

    type = Enum(choices=['income_statement', 'balance_sheet', 'cash_flow', 'equity_statement',
                         'comprehensive_income'], nullable=False, index=True)

    data = JSONField(nullable=False)

    filing_id = Integer(nullable=False, foreign_key="filings.id", on_delete="CASCADE", index=True)
    company_id = Integer(nullable=False, foreign_key="companies.id", on_delete="CASCADE", index=True)

    created_at = Timestamp(nullable=False, default="CURRENT_TIMESTAMP")
    updated_at = Timestamp(nullable=False, default="CURRENT_TIMESTAMP", auto_update=True)

    __uniques__ = [("filing_id", "type")]


class FilingNotes(Table):
    __tablename__ = "filing_notes"

    id = Serial()

    title = Text(nullable=False)
    preview = Text(nullable=True)
    filename = Text(nullable=False)
    content = Text(nullable=False, fts=True)

    filing_id = Integer(nullable=False, foreign_key="filings.id", on_delete="CASCADE", index=True)
    company_id = Integer(nullable=False, foreign_key="companies.id", on_delete="CASCADE", index=True)

    created_at = Timestamp(nullable=False, default="CURRENT_TIMESTAMP")
    updated_at = Timestamp(nullable=False, default="CURRENT_TIMESTAMP", auto_update=True)

    __uniques__ = [("filing_id", "filename")]


class FilingNoteChunks(Table):
    __tablename__ = "filing_note_chunks"

    id = Serial()

    index = Integer(nullable=False)
    content = Text(nullable=False, fts=True)
    embedding = Text(nullable=False, fts=True, vector=True)
    has_table = Boolean(nullable=False, default=False, index=True)

    filing_note_id = Integer(nullable=False, foreign_key="filing_notes.id", on_delete="CASCADE", index=True)
    filing_id = Integer(nullable=False, foreign_key="filings.id", on_delete="CASCADE", index=True)
    company_id = Integer(nullable=False, foreign_key="companies.id", on_delete="CASCADE", index=True)

    created_at = Timestamp(nullable=False, default="CURRENT_TIMESTAMP")
    updated_at = Timestamp(nullable=False, default="CURRENT_TIMESTAMP", auto_update=True)

    __uniques__ = [("filing_note_id", "index")]


class FilingSectionPages(Table):
    __tablename__ = "filing_section_pages"

    id = Serial()

    section = Enum(choices=[
        "business",  # 10-K Item 1
        "risk_factors",  # 10-K Item 1A / 10-Q Part II Item 1A
        "properties",  # 10-K Item 2
        "legal_proceedings",  # 10-K Item 3 / 10-Q Part II Item 1
        "market_equity_matters",  # 10-K Item 5
        "selected_financial_data",  # 10-K Item 6 (eliminated by SEC in 2021, now shows as [Reserved])
        "md&a",  # 10-K Item 7 / 10-Q Part I Item 2
        "market_risk",  # 10-K Item 7A / 10-Q Part I Item 3
        "controls_procedures",  # 10-K Item 9A / 10-Q Part I Item 4
        "other_information",  # 10-K Item 9B
        "unregistered_sales_equity",  # 10-Q Part II Item 2 (buybacks, private placements)
        "other"  # fallback (store raw heading)
    ], nullable=False, index=True)

    page = Integer(nullable=False, index=True)
    content = Text(nullable=False, fts=True)
    has_table = Boolean(default=False, index=True)

    filing_id = Integer(nullable=False, foreign_key="filings.id", on_delete="CASCADE", index=True)
    company_id = Integer(nullable=False, foreign_key="companies.id", on_delete="CASCADE", index=True)

    created_at = Timestamp(nullable=False, default="CURRENT_TIMESTAMP")
    updated_at = Timestamp(nullable=False, default="CURRENT_TIMESTAMP", auto_update=True)

    __uniques__ = [("filing_id", "section", "page")]


class FilingSectionChunks(Table):
    __tablename__ = "filing_section_chunks"

    id = Serial()

    section = Enum(choices=[
        "business",  # 10-K Item 1
        "risk_factors",  # 10-K Item 1A / 10-Q Part II Item 1A
        "properties",  # 10-K Item 2
        "legal_proceedings",  # 10-K Item 3 / 10-Q Part II Item 1
        "market_equity_matters",  # 10-K Item 5
        "selected_financial_data",  # 10-K Item 6 (eliminated by SEC in 2021, now shows as [Reserved])
        "md&a",  # 10-K Item 7 / 10-Q Part I Item 2
        "market_risk",  # 10-K Item 7A / 10-Q Part I Item 3
        "controls_procedures",  # 10-K Item 9A / 10-Q Part I Item 4
        "other_information",  # 10-K Item 9B
        "unregistered_sales_equity",  # 10-Q Part II Item 2 (buybacks, private placements)
        "other"  # fallback (store raw heading)
    ], nullable=False, index=True)

    index = Integer(nullable=False)
    page = Integer(nullable=False, index=True)  # original page number
    pages = JSONField(nullable=False)  # List of {page: int, content: str}
    embedding = Text(nullable=False, fts=True, vector=True)
    has_table = Boolean(default=False, index=True)

    filing_id = Integer(nullable=False, foreign_key="filings.id", on_delete="CASCADE", index=True)
    company_id = Integer(nullable=False, foreign_key="companies.id", on_delete="CASCADE", index=True)

    created_at = Timestamp(nullable=False, default="CURRENT_TIMESTAMP")
    updated_at = Timestamp(nullable=False, default="CURRENT_TIMESTAMP", auto_update=True)

    __uniques__ = [("filing_id", "section", "index")]


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
    title = Field(table="filings", field="title")
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


class CompanyFilingNoteChunks(View):
    __viewname__ = "company_filing_note_chunks"
    __tables__ = (FilingNoteChunks, FilingNotes, Filings, Companies)

    id = Field(table="filing_note_chunks", field="id")
    index = Field(table="filing_note_chunks", field="index")
    content = Field(table="filing_note_chunks", field="content")
    embedding = Field(table="filing_note_chunks", field="embedding")
    has_table = Field(table="filing_note_chunks", field="has_table")
    filing_note_id = Field(table="filing_note_chunks", field="filing_note_id")

    note_title = Field(table="filing_notes", field="title")
    note_filename = Field(table="filing_notes", field="filename")

    filing_id = Field(table="filing_note_chunks", field="filing_id")
    form = Field(table="filings", field="form")
    amendment = Field(table="filings", field="amendment")
    fiscal_year = Field(table="filings", field="fiscal_year")
    fiscal_period = Field(table="filings", field="fiscal_period")
    filing_date = Field(table="filings", field="filing_date")
    report_date = Field(table="filings", field="report_date")
    accession_number = Field(table="filings", field="accession_number")

    company_id = Field(table="filing_note_chunks", field="company_id")
    company_name = Field(table="companies", field="name")
    company_symbols = Field(table="companies", field="symbols")


class CompanyFilingSectionChunks(View):
    __viewname__ = "company_filing_section_chunks"
    __tables__ = (FilingSectionChunks, Filings, Companies)

    id = Field(table="filing_section_chunks", field="id")
    section = Field(table="filing_section_chunks", field="section")
    index = Field(table="filing_section_chunks", field="index")
    page = Field(table="filing_section_chunks", field="page")
    pages = Field(table="filing_section_chunks", field="pages")
    embedding = Field(table="filing_section_chunks", field="embedding")
    has_table = Field(table="filing_section_chunks", field="has_table")

    filing_id = Field(table="filing_section_chunks", field="filing_id")
    form = Field(table="filings", field="form")
    amendment = Field(table="filings", field="amendment")
    items = Field(table="filings", field="items")
    press_release = Field(table="filings", field="press_release")
    fiscal_year = Field(table="filings", field="fiscal_year")
    fiscal_period = Field(table="filings", field="fiscal_period")
    filing_date = Field(table="filings", field="filing_date")
    report_date = Field(table="filings", field="report_date")
    accession_number = Field(table="filings", field="accession_number")

    company_id = Field(table="filing_section_chunks", field="company_id")
    company_name = Field(table="companies", field="name")
    company_symbols = Field(table="companies", field="symbols")
    company_exchanges = Field(table="companies", field="exchanges")
    company_sector = Field(table="companies", field="sector")
    company_industry = Field(table="companies", field="industry")


class CompanyFinancialStatements(View):
    __viewname__ = "company_financial_statements"
    __tables__ = (FinancialStatements, Filings, Companies)

    id = Field(table="financial_statements", field="id")
    type = Field(table="financial_statements", field="type")
    data = Field(table="financial_statements", field="data")

    filing_id = Field(table="financial_statements", field="filing_id")
    form = Field(table="filings", field="form")
    amendment = Field(table="filings", field="amendment")
    fiscal_year = Field(table="filings", field="fiscal_year")
    fiscal_period = Field(table="filings", field="fiscal_period")
    filing_date = Field(table="filings", field="filing_date")
    report_date = Field(table="filings", field="report_date")
    accession_number = Field(table="filings", field="accession_number")

    company_id = Field(table="financial_statements", field="company_id")
    company_name = Field(table="companies", field="name")
    company_symbols = Field(table="companies", field="symbols")
    company_cik = Field(table="companies", field="cik")


class FilingAttachments(Table):
    __tablename__ = "filing_attachments"

    id = Serial()

    exhibit_number = Text(nullable=False, index=True)
    filename = Text(nullable=False)
    description = Text(nullable=True)
    num_pages = Integer(nullable=True)
    type = Enum(
        choices=['press_release', 'material_contract', 'corporate_governance', 'debt_securities',
                 'merger_acquisition', 'subsidiaries', 'legal_compliance', 'other'],
        nullable=False,
        default='other',
        index=True
    )
    title = Text(nullable=True, fts=True)  # LLM-generated title
    summary = Text(nullable=True, fts=True, vector=True)  # LLM-generated summary (context from header baked in)

    filing_id = Integer(nullable=False, foreign_key="filings.id", on_delete="CASCADE", index=True)
    company_id = Integer(nullable=False, foreign_key="companies.id", on_delete="CASCADE", index=True)

    created_at = Timestamp(nullable=False, default="CURRENT_TIMESTAMP")
    updated_at = Timestamp(nullable=False, default="CURRENT_TIMESTAMP", auto_update=True)

    __uniques__ = [("filing_id", "exhibit_number")]


class FilingAttachmentPages(Table):
    __tablename__ = "filing_attachment_pages"

    id = Serial()
    page = Integer(nullable=False, index=True)
    content = Text(nullable=False, fts=True)

    attachment_id = Integer(nullable=False, foreign_key="filing_attachments.id", on_delete="CASCADE", index=True)
    filing_id = Integer(nullable=False, foreign_key="filings.id", on_delete="CASCADE", index=True)
    company_id = Integer(nullable=False, foreign_key="companies.id", on_delete="CASCADE", index=True)

    created_at = Timestamp(nullable=False, default="CURRENT_TIMESTAMP")
    updated_at = Timestamp(nullable=False, default="CURRENT_TIMESTAMP", auto_update=True)

    __uniques__ = [("attachment_id", "page")]


class FilingAttachmentChunks(Table):
    __tablename__ = "filing_attachment_chunks"

    id = Serial()
    index = Integer(nullable=False)
    page = Integer(nullable=False)
    pages = JSONField(nullable=False)  # List of {page: int, content: str}
    embedding = Text(nullable=False, fts=True, vector=True)  # Header + content for vector search
    has_table = Boolean(nullable=False, default=False, index=True)

    attachment_id = Integer(nullable=False, foreign_key="filing_attachments.id", on_delete="CASCADE", index=True)
    filing_id = Integer(nullable=False, foreign_key="filings.id", on_delete="CASCADE", index=True)
    company_id = Integer(nullable=False, foreign_key="companies.id", on_delete="CASCADE", index=True)

    created_at = Timestamp(nullable=False, default="CURRENT_TIMESTAMP")
    updated_at = Timestamp(nullable=False, default="CURRENT_TIMESTAMP", auto_update=True)

    __uniques__ = [("attachment_id", "index")]


class EarningsTranscripts(Table):
    __tablename__ = "earnings_transcripts"

    id = Serial()
    fiscal_year: Integer(nullable=False)
    fiscal_period = Enum(choices=['Q1', 'Q2', 'Q3', 'Q4', 'FY'], nullable=False)

    sections: JSONField(nullable=False)  # A list of formatted JSON sections

    company_id = Integer(nullable=False, foreign_key="companies.id", on_delete="CASCADE", index=True)

    created_at = Timestamp(nullable=False, default="CURRENT_TIMESTAMP")
    updated_at = Timestamp(nullable=False, default="CURRENT_TIMESTAMP", auto_update=True)

    __uniques__ = [("company_id", "fiscal_year", "fiscal_period")]


class EarningsTranscriptChunks(Table):
    __tablename__ = "earnings_transcripts"

    id = Serial()

    index: Integer(nullable=False)

    sections: JSONField(nullable=False)
    embedding: Text(nullable=False, fts=True, vector=True)

    transcript_id = Integer(nullable=False, foreign_key="earnings_transcripts.id", on_delete="CASCADE", index=True)
    company_id = Integer(nullable=False, foreign_key="companies.id", on_delete="CASCADE", index=True)

    created_at = Timestamp(nullable=False, default="CURRENT_TIMESTAMP")
    updated_at = Timestamp(nullable=False, default="CURRENT_TIMESTAMP", auto_update=True)

    __uniques__ = [("transcript_id", "index")]


class CompanyFilingAttachments(View):
    __viewname__ = "company_filing_attachments"
    __tables__ = (FilingAttachments, Filings, Companies)

    id = Field(table="filing_attachments", field="id")
    exhibit_number = Field(table="filing_attachments", field="exhibit_number")
    filename = Field(table="filing_attachments", field="filename")
    description = Field(table="filing_attachments", field="description")
    title = Field(table="filing_attachments", field="title")
    num_pages = Field(table="filing_attachments", field="num_pages")
    attachment_type = Field(table="filing_attachments", field="type")

    filing_id = Field(table="filing_attachments", field="filing_id")
    form = Field(table="filings", field="form")
    amendment = Field(table="filings", field="amendment")
    items = Field(table="filings", field="items")
    fiscal_year = Field(table="filings", field="fiscal_year")
    fiscal_period = Field(table="filings", field="fiscal_period")
    filing_date = Field(table="filings", field="filing_date")
    report_date = Field(table="filings", field="report_date")
    accession_number = Field(table="filings", field="accession_number")

    company_id = Field(table="filing_attachments", field="company_id")
    company_name = Field(table="companies", field="name")
    company_symbols = Field(table="companies", field="symbols")
    company_exchanges = Field(table="companies", field="exchanges")
    company_sector = Field(table="companies", field="sector")
    company_industry = Field(table="companies", field="industry")


class CompanyFilingAttachmentPages(View):
    __viewname__ = "company_filing_attachment_pages"
    __tables__ = (FilingAttachmentPages, FilingAttachments, Filings, Companies)

    id = Field(table="filing_attachment_pages", field="id")
    page = Field(table="filing_attachment_pages", field="page")
    content = Field(table="filing_attachment_pages", field="content")

    attachment_id = Field(table="filing_attachment_pages", field="attachment_id")
    exhibit_number = Field(table="filing_attachments", field="exhibit_number")
    attachment_filename = Field(table="filing_attachments", field="filename")
    attachment_description = Field(table="filing_attachments", field="description")
    attachment_type = Field(table="filing_attachments", field="type")

    filing_id = Field(table="filing_attachment_pages", field="filing_id")
    form = Field(table="filings", field="form")
    amendment = Field(table="filings", field="amendment")
    items = Field(table="filings", field="items")
    fiscal_year = Field(table="filings", field="fiscal_year")
    fiscal_period = Field(table="filings", field="fiscal_period")
    filing_date = Field(table="filings", field="filing_date")
    report_date = Field(table="filings", field="report_date")
    accession_number = Field(table="filings", field="accession_number")

    company_id = Field(table="filing_attachment_pages", field="company_id")
    company_name = Field(table="companies", field="name")
    company_symbols = Field(table="companies", field="symbols")
    company_exchanges = Field(table="companies", field="exchanges")
    company_sector = Field(table="companies", field="sector")
    company_industry = Field(table="companies", field="industry")


class CompanyFilingAttachmentChunks(View):
    __viewname__ = "company_filing_attachment_chunks"
    __tables__ = (FilingAttachmentChunks, FilingAttachments, Filings, Companies)

    id = Field(table="filing_attachment_chunks", field="id")
    index = Field(table="filing_attachment_chunks", field="index")
    page = Field(table="filing_attachment_chunks", field="page")
    pages = Field(table="filing_attachment_chunks", field="pages")
    embedding = Field(table="filing_attachment_chunks", field="embedding")
    has_table = Field(table="filing_attachment_chunks", field="has_table")

    attachment_id = Field(table="filing_attachment_chunks", field="attachment_id")
    exhibit_number = Field(table="filing_attachments", field="exhibit_number")
    attachment_filename = Field(table="filing_attachments", field="filename")
    attachment_description = Field(table="filing_attachments", field="description")
    attachment_type = Field(table="filing_attachments", field="type")

    filing_id = Field(table="filing_attachment_chunks", field="filing_id")
    form = Field(table="filings", field="form")
    amendment = Field(table="filings", field="amendment")
    items = Field(table="filings", field="items")
    fiscal_year = Field(table="filings", field="fiscal_year")
    fiscal_period = Field(table="filings", field="fiscal_period")
    filing_date = Field(table="filings", field="filing_date")
    report_date = Field(table="filings", field="report_date")
    accession_number = Field(table="filings", field="accession_number")

    company_id = Field(table="filing_attachment_chunks", field="company_id")
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
schema.add_table(FilingAttachments)
schema.add_table(FilingAttachmentPages)
schema.add_table(FilingAttachmentChunks)
schema.add_table(FilingSectionChunks)
schema.add_table(FilingNoteChunks)
schema.add_table(FilingSectionPages)

schema.add_view(CompanyFilings)
schema.add_view(CompanyFilingPages)
schema.add_view(CompanyFilingNotes)
schema.add_view(CompanyFilingNoteChunks)
schema.add_view(CompanyFilingSectionChunks)
schema.add_view(CompanyFinancialStatements)
schema.add_view(CompanyFilingAttachments)
schema.add_view(CompanyFilingAttachmentPages)
schema.add_view(CompanyFilingAttachmentChunks)
