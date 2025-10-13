# from edgar.entity.filings import EntityFiling
#
# from database import Database
#
#
# class Filing:
#
#     def __init__(self, filing: EntityFiling, company: dict, database: Database):
#         self.filing = filing
#         self.company = company
#         self.company_id = company['id']
#         self.database = database
#
#         self.accession_number = filing.accession_number
#         self.report_date = filing.report_date if filing.report_date else None
#         self.filing_date = filing.filing_date.strftime('%Y-%m-%d')
#
#         self.markdown_chunker = MarkdownChunker()
#
#     async def upsert(self):
#         """Upsert the filing + data"""
#         pass
