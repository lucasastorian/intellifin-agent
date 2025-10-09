import asyncio
from typing import List, Dict
from edgar.xbrl import XBRL

from pipeline.filings.base_filing import BaseFiling
from pipeline.parsers.parser import Parser
from pipeline.enrichment.openai_client import OpenAIClient
from pipeline.enrichment.filing_summarizer import FilingSummarizer


class FilingEightK(BaseFiling):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        openai_client = OpenAIClient()
        self.filing_summarizer = FilingSummarizer(openai_client)

    def upsert(self):
        """Upserts the 8-K filing"""
        xbrl = self.filing.xbrl()

        filing = self._upsert_filing(xbrl=xbrl)
        pages = self._upsert_filing_pages(filing_id=filing['id'])
        attachment_data = self._upsert_attachments(filing_id=filing['id'])

        # Run async enrichment in this thread's event loop
        async def enrich():
            enriched_attachments = await self._enrich_attachments(attachment_data, filing)
            await self._enrich_filing(filing, pages, enriched_attachments)

        asyncio.run(enrich())

        self._update_filing_counts(filing_id=filing['id'])

    def _upsert_filing(self, xbrl: XBRL) -> dict:
        """Creates a filing record"""
        response = self.database.table("filings").upsert({
            "form": self.filing.form,
            "items": self.filing.items.split(','),
            "press_release": '9.01' in self.filing.items,
            "amendment": self.filing.form == "8-K/A",
            "filing_date": self.filing_date,
            "report_date": self.report_date,
            "accession_number": self.accession_number,
            "company_id": self.company_id
        }, on_conflict="accession_number").execute()

        return response.data[0]

    def _upsert_attachments(self, filing_id: int) -> List[Dict]:
        """Upserts ALL attachments (exhibits) for 8-K filings, returns data for enrichment"""
        documents = self.filing.attachments.documents
        attachment_data = []

        for document in documents:
            if not document.document_type or not document.document_type.startswith("EX-"):
                continue

            exhibit_number = document.document_type.replace("EX-", "")

            if not document.is_html():
                continue

            parser = Parser(content=document.content)
            pages = parser.get_pages()

            if not pages:
                continue

            attachment_type = self.infer_attachment_type(exhibit_number)

            attachment_response = self.database.table("filing_attachments").upsert({
                "exhibit_number": exhibit_number,
                "filename": document.document or f"ex-{exhibit_number}",
                "description": document.description,
                "num_pages": len(pages),
                "type": attachment_type,
                "filing_id": filing_id,
                "company_id": self.company_id
            }, on_conflict="filing_id,exhibit_number").execute()

            if not attachment_response.data:
                continue

            attachment_id = attachment_response.data[0]['id']

            self.database.table("filing_attachment_pages").upsert([{
                "page": page['page'],
                "content": page['content'],
                "attachment_id": attachment_id,
                "filing_id": filing_id,
                "company_id": self.company_id
            } for page in pages], on_conflict="attachment_id,page").execute()

            self._upsert_filing_attachment_chunks(pages=pages, attachment_id=attachment_id, filing_id=filing_id)

            attachment_data.append({
                "attachment_id": attachment_id,
                "exhibit_number": exhibit_number,
                "pages": pages[:10]
            })

        return attachment_data

    async def _enrich_filing(self, filing: dict, pages: List[Dict], enriched_attachments: List[Dict]):
        """Generate LLM summary for the filing using attachment summaries + filing content"""
        # Build header
        header = self.filing_summarizer.build_header(self.company, filing)

        # Get first 10 pages
        first_pages = pages[:10]

        # Generate filing summary
        result = await self.filing_summarizer.summarize(
            pages=first_pages,
            attachment_summaries=enriched_attachments,
            header=header
        )

        # Update filing with title + summary
        if result and (result.get("title") or result.get("summary")):
            self.database.table("filings").update({
                "title": result["title"],
                "summary": result["summary"]
            }).eq("id", filing['id']).execute()
