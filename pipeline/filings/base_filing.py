import asyncio
from bs4 import BeautifulSoup
from abc import ABC, abstractmethod
from typing import Optional, List, Literal, Dict
from edgar.entity.filings import EntityFiling
from edgar.xbrl import XBRL

from database.database import Database
from pipeline.parsers.parser import Parser
from pipeline.chunker.markdown_chunker import MarkdownChunker
from pipeline.parsers.financial_statement import FinancialStatements
from pipeline.enrichment.openai_client import OpenAIClient
from pipeline.enrichment.attachment_summarizer import AttachmentSummarizer
from pipeline.enrichment.note_preview_generator import NotePreviewGenerator
from pipeline.enrichment.note_embedding_generator import NoteEmbeddingGenerator
from pipeline.enrichment.attachment_embedding_generator import AttachmentEmbeddingGenerator


class BaseFiling(ABC):

    def __init__(self, filing: EntityFiling, company: dict, database: Database):
        self.filing = filing
        self.company = company
        self.company_id = company['id']
        self.database = database

        self.accession_number = filing.accession_number
        self.report_date = filing.report_date if filing.report_date else None
        self.filing_date = filing.filing_date.strftime('%Y-%m-%d')

        self.markdown_chunker = MarkdownChunker()

        # Each subclass that needs OpenAI should instantiate its own client
        openai_client = OpenAIClient()
        self.attachment_summarizer = AttachmentSummarizer(openai_client)
        self.note_preview_generator = NotePreviewGenerator(openai_client)

    @abstractmethod
    async def upsert(self):
        """Upserts the filing and associated data to local DB"""
        raise NotImplementedError

    @abstractmethod
    def _upsert_filing(self, xbrl: XBRL):
        """Upserts the filing to the local db"""
        raise NotImplementedError

    async def _load_xbrl(self) -> XBRL:
        """Load XBRL using async SGML loading (caches result)"""
        await self.filing.sgml_async()  # Cache SGML
        return self.filing.xbrl()  # Use cached SGML

    async def _load_html(self) -> str:
        """Load HTML using async SGML loading (caches result)"""
        await self.filing.sgml_async()  # Cache SGML
        return self.filing.html()

    @staticmethod
    def infer_attachment_type(exhibit_number: str) -> str:
        """Infer attachment type from exhibit number

        Args:
            exhibit_number: Exhibit number without "EX-" prefix (e.g., "99.1", "10.2", "3.1")

        Returns:
            Type string matching the granular types in FilingAttachments enum
        """
        parts = exhibit_number.split(".")
        prefix = parts[0]
        suffix = parts[1] if len(parts) > 1 else None

        # Underwriting agreements (1.x)
        if prefix == "1":
            return "underwriting_agreement"

        # Merger agreements (2.1)
        elif prefix == "2":
            if suffix == "1":
                return "merger_agreement"
            return "other"

        # Charter/bylaws (3.x)
        elif prefix == "3":
            if suffix == "1":
                return "certificate_of_designations"
            elif suffix == "2":
                return "bylaws"
            else:
                return "charter"

        # Debt instruments (4.x)
        elif prefix == "4":
            if suffix == "1":
                return "indenture"
            elif suffix == "2":
                return "supplemental_indenture"
            else:
                return "debt_instrument"

        # Legal opinions (5.x)
        elif prefix == "5":
            return "legal_opinion"

        # Material contracts (10.x)
        elif prefix == "10":
            return "material_contract"

        # Subsidiaries (21)
        elif prefix == "21":
            return "subsidiaries"

        # Consents (23.x)
        elif prefix == "23":
            return "consent"

        # Press releases and investor presentations (99.x)
        elif prefix == "99":
            return "press_or_investor"

        else:
            return "other"

    async def exists(self):
        """Returns True if an entry for the filing exists in the DB"""
        result = await self.database.table("filings").select("*").eq("accession_number",
                                                                     self.accession_number).execute()
        return len(result.data) > 0

    async def _mark_synced(self, filing_id: int):
        """Mark this filing as synced in the database"""
        await self.database.table("filings").update({"synced": True}).eq("id", filing_id).execute()

    async def _upsert_filing_pages(self, filing_id: int):
        """Creates a record for the filing pages and returns the raw pages"""
        html_content = await self._load_html()

        parser = Parser(content=html_content)
        pages = parser.get_pages()

        response = await self.database.table("filing_pages").upsert([{
            "page": page['page'],
            "content": page['content'],
            "filing_id": filing_id,
            "company_id": self.company_id
        } for page in pages], on_conflict="filing_id,page").execute()

        return pages

    async def _upsert_filing_notes(self, filing_id: int):
        """Upserts all the notes associated with the filing and their chunks"""
        if not self.filing.reports:
            return None

        notes = self.filing.reports.get_by_category("Notes")
        processed_notes = []

        for note in notes:
            note_content = self._flatten_note(content=note.content)
            parser = Parser(content=note_content)
            note_markdown = parser.markdown()
            processed_notes.append({"title": note.short_name, "content": note_markdown,
                                    "filename": note.html_file_name, "filing_id": filing_id,
                                    "company_id": self.company_id})

        response = await self.database.table("filing_notes").upsert(processed_notes,
                                                                    on_conflict="filing_id,filename").execute()

        note_ids = [note['id'] for note in response.data]

        return note_ids, processed_notes

    async def _upsert_filing_note_chunks(self, note_ids: list, processed_notes: list, filing: dict):
        """Chunks filing notes and upserts each note separately for contextualized embeddings"""
        generator = NoteEmbeddingGenerator(company=self.company, filing=filing, chunk_size=2048, chunk_overlap=0)

        # Upsert each note's chunks separately to preserve document boundaries
        # for contextualized embeddings (voyage-context-3 compatibility)
        for note_id, note_data in zip(note_ids, processed_notes):
            chunks = await generator.embed(note_title=note_data['title'], note_content=note_data['content'])

            note_chunks = []
            for i, chunk in enumerate(chunks):
                note_chunks.append({
                    "index": i,
                    "content": chunk.content,
                    "embedding": chunk.embedding_text,  # Uses header + content
                    "has_table": chunk.has_table,
                    "filing_note_id": note_id,
                    "filing_id": filing['id'],
                    "company_id": self.company_id
                })

            if note_chunks:
                await self.database.table("filing_note_chunks").upsert(
                    note_chunks,
                    on_conflict="filing_note_id,index"
                ).execute()

    async def _upsert_financial_statements(self, xbrl: Optional[XBRL], filing_id: int):
        """Upserts the financial statements for 10-Ks/10-Qs/20-Fs"""
        if xbrl is None:
            return

        fiscal_period = xbrl.entity_info.get('fiscal_period') if xbrl else None
        statements = FinancialStatements(
            xbrl=xbrl,
            report_date=self.report_date,
            filing_id=filing_id,
            company_id=self.company_id,
            database=self.database,
            fiscal_period=fiscal_period,
            form=self.filing.form,
            accession_number=self.accession_number
        )
        await statements.upsert_statements()

    @staticmethod
    def _flatten_note(content: str) -> Optional[str]:
        """Flattens the note structure by removing the outer table"""
        soup = BeautifulSoup(content, 'lxml')
        elements = []

        body = soup.find("body")
        if not body:
            return None

        table = body.find("table")
        if table is None:
            return None

        for row in table.find_all('tr', recursive=False):
            cells = row.find_all(['th', 'td'], recursive=False)

            for cell in cells:
                elements.append(cell)

        if len(elements) == 0:
            return None

        return ''.join([str(element) for element in elements])

    async def _update_filing_counts(self, num_pages: int, num_attachments: int, filing_id: int):
        """Updates the filing with page count and attachment count"""
        await self.database.table("filings").update({
            "num_pages": num_pages,
            "num_attachments": num_attachments
        }).eq("id", filing_id).execute()

    async def _upsert_attachments_and_pages(self, filing_id: int):
        """Upserts all the attachments for a filing"""
        # Define which attachment types should be chunked
        CHUNKABLE_TYPES = {
            "underwriting_agreement",
            "merger_agreement",
            "certificate_of_designations",
            "indenture",
            "supplemental_indenture",
            "debt_instrument",
            "press_or_investor"
        }

        documents = self.filing.attachments.documents
        attachments = []
        attachment_pages = {}
        chunkable_attachments = []  # Collect attachments that need chunking

        for document in documents:
            if not document.document_type or not document.document_type.startswith("EX-"):
                continue

            exhibit_number = document.document_type.replace("EX-", "")

            # # NOTE -> Hardcode this here...
            # if exhibit_filter and not exhibit_filter(exhibit_number):
            #     continue

            if not document.is_html():
                continue

            parser = Parser(content=document.content)
            pages = parser.get_pages()

            if not pages:
                continue

            attachment_type = self.infer_attachment_type(exhibit_number)

            attachment_pages[exhibit_number] = pages

            attachments.append(
                {
                    "exhibit_number": exhibit_number,
                    "filename": document.document or f"ex-{exhibit_number}",
                    "description": document.description,
                    "num_pages": len(pages),
                    "type": attachment_type,
                    "filing_id": filing_id,
                    "company_id": self.company_id
                }
            )

            # Collect attachments that should be chunked
            if attachment_type in CHUNKABLE_TYPES:
                chunkable_attachments.append({
                    "exhibit_number": exhibit_number,
                    "pages": pages,
                    "description": document.description,
                    "attachment_type": attachment_type
                })

        response = await self.database.table("filing_attachments").upsert(attachments,
                                                                          on_conflict="filing_id,exhibit_number").execute()

        exhibit_to_attachment_id = {
            att['exhibit_number']: att['id']
            for att in response.data
        }

        all_pages = [
            {
                "page": page['page'],
                "content": page['content'],
                "attachment_id": exhibit_to_attachment_id[exhibit_number],
                "filing_id": filing_id,
                "company_id": self.company_id
            }
            for exhibit_number, pages in attachment_pages.items()
            for page in pages
        ]

        if all_pages:
            await self.database.table("filing_attachment_pages").upsert(
                all_pages,
                on_conflict="attachment_id,page"
            ).execute()

        # Chunk all chunkable attachments
        if chunkable_attachments:
            await self._upsert_attachment_chunks(
                chunkable_attachments=chunkable_attachments,
                exhibit_to_attachment_id=exhibit_to_attachment_id,
                filing_id=filing_id
            )

        return attachments

    async def _upsert_attachment_chunks(
            self,
            chunkable_attachments: list,
            exhibit_to_attachment_id: dict,
            filing_id: int
    ):
        """Upserts chunks for each attachment separately for contextualized embeddings"""
        filing_data = {
            "form": self.filing.form,
            "filing_date": self.filing_date,
            "report_date": self.report_date
        }

        generator = AttachmentEmbeddingGenerator(self.company, filing_data, chunk_size=1024, chunk_overlap=0)

        for attachment_data in chunkable_attachments:
            exhibit_number = attachment_data["exhibit_number"]
            pages = attachment_data["pages"]
            attachment_id = exhibit_to_attachment_id[exhibit_number]
            description = attachment_data["description"]
            attachment_type = attachment_data["attachment_type"]

            chunks = await generator.embed(pages, attachment_type, exhibit_number, description)

            attachment_chunks = []
            for i, chunk in enumerate(chunks):
                attachment_chunks.append({
                    "index": i,
                    "page": chunk.page,
                    "pages": chunk.pages,
                    "embedding": chunk.embedding_text,
                    "has_table": chunk.has_table,
                    "attachment_id": attachment_id,
                    "filing_id": filing_id,
                    "company_id": self.company_id
                })

            if attachment_chunks:
                await self.database.table("filing_attachment_chunks").upsert(
                    attachment_chunks,
                    on_conflict="attachment_id,index"
                ).execute()

    #
    # async def _enrich_attachments(self, attachment_data: List[Dict], filing: dict) -> List[Dict]:
    #     """Generate LLM summaries for all attachments in parallel, returns enriched attachments"""
    #     if not attachment_data:
    #         return []
    #
    #     header = self.attachment_summarizer.build_header(self.company, filing)
    #
    #     tasks = [
    #         self.attachment_summarizer.summarize(att["pages"], header)
    #         for att in attachment_data
    #     ]
    #
    #     summaries = await asyncio.wait_for(
    #         asyncio.gather(*tasks, return_exceptions=True),
    #         timeout=180
    #     )
    #
    #     updates = []
    #     enriched = []
    #     for att, result in zip(attachment_data, summaries):
    #         if isinstance(result, Exception):
    #             print(f"ERROR enriching attachment {att['exhibit_number']}: {result}")
    #             import traceback
    #             traceback.print_exception(type(result), result, result.__traceback__)
    #             continue
    #
    #         if result and (result.get("title") or result.get("summary")):
    #             # Only pass changed fields - UpsertBuilder will pull missing NOT NULL fields from existing row
    #             updates.append({
    #                 "id": att["attachment_id"],
    #                 "title": result["title"],
    #                 "summary": result["summary"]
    #             })
    #             enriched.append({
    #                 "exhibit_number": att.get("exhibit_number", "Unknown"),
    #                 "title": result["title"],
    #                 "summary": result["summary"]
    #             })
    #         else:
    #             print(f"WARNING: Attachment {att['exhibit_number']} returned empty result: {result}")
    #
    #     # Batch upsert all attachments at once (SELECT-based INSERT pulls missing fields)
    #     if updates:
    #         await self.database.table("filing_attachments").upsert(
    #             updates,
    #             on_conflict="id"
    #         ).execute()
    #
    #     return enriched
    #
    # async def _enrich_note_previews(self, note_ids: List[int], filing: dict):
    #     """Generate one-sentence previews for all notes in parallel
    #
    #     Args:
    #         note_ids: List of note IDs to enrich
    #         filing: Filing dict with metadata
    #     """
    #     if not note_ids:
    #         return
    #
    #     # print(f"  INFO: Starting note preview enrichment for {len(note_ids)} notes in parallel...")
    #
    #     # Use self.company and filing dict directly (no DB fetch needed!)
    #     # Fetch note titles and content for preview generation
    #     notes = await self.database.table("filing_notes").select("id,title,content").in_("id", note_ids).execute()
    #
    #     tasks = [
    #         self.note_preview_generator.generate(note['title'], note['content'], self.company, filing)
    #         for note in notes.data
    #     ]
    #
    #     try:
    #         previews = await asyncio.wait_for(
    #             asyncio.gather(*tasks, return_exceptions=True),
    #             timeout=180
    #         )
    #     except asyncio.TimeoutError:
    #         print(f"WARNING: Note preview enrichment timed out after 30s")
    #         previews = [Exception("Timeout") for _ in tasks]
    #
    #     updates = []
    #     for note, result in zip(notes.data, previews):
    #         if isinstance(result, Exception):
    #             continue
    #
    #         if result:
    #             # Only pass changed fields - UpsertBuilder will pull missing NOT NULL fields from existing row
    #             updates.append({
    #                 "id": note['id'],
    #                 "preview": result
    #             })
    #
    #     # Batch upsert all notes at once (SELECT-based INSERT pulls missing fields)
    #     if updates:
    #         await self.database.table("filing_notes").upsert(
    #             updates,
    #             on_conflict="id"
    #         ).execute()
