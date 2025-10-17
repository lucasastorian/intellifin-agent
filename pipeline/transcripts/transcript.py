import datetime
from typing import List, Dict

from database import Database
from pipeline.transcripts.transcript_parser import TranscriptParser
from pipeline.transcripts.transcript_splitter import TranscriptSplitter


class Transcript:

    def __init__(self, content: str, fiscal_year: int, fiscal_quarter: int, date: str, company: dict,
                 database: Database):
        self.content = content
        self.fiscal_year = fiscal_year
        self.fiscal_quarter = fiscal_quarter
        self.date = date
        self.company = company
        self.database = database

        self.parser = TranscriptParser()

    async def upsert(self):
        """Upserts the transcript, and the relevant chunks"""
        async with self.database.batch_embeddings():
            sections = self.parser.parse(content=self.content)
            transcript_id = await self._upsert_transcript(sections=sections)

            await self._upsert_transcript_chunks(sections=sections, transcript_id=transcript_id)

        await self.database.table("earnings_transcripts").update({"synced": True}).eq("id", transcript_id).execute()

    async def _upsert_transcript(self, sections: List[Dict[str, str]]) -> int:
        """Upserts the transcript and returns the transcript ID"""
        dt = datetime.datetime.strptime(self.date, "%Y-%m-%d %H:%M:%S")
        fiscal_period = f"Q{self.fiscal_quarter}" if self.fiscal_quarter != 3 else "FY"

        response = await self.database.table("earnings_transcripts").upsert({
            "sections": sections,
            "fiscal_year": self.fiscal_year,
            "fiscal_period": fiscal_period,
            "date": dt.isoformat(),
            "company_id": self.company['id']
        }, on_conflict="company_id,fiscal_year,fiscal_period").execute()

        return response.data[0]['id']

    async def _upsert_transcript_chunks(self, sections: List[Dict[str, str]], transcript_id: int):
        """Chunks the transcript and upserts them"""
        # Splits the transcript by 'Operator' to seperate different contiguous Q&A sections
        segments = TranscriptSplitter().split(sections=sections)

        data = []

        for i, segment in enumerate(segments, 1):
            data.append({
                "index": i,
                "sections": segment,
                "embedding": self._format_segment(segment=segment),
                "transcript_id": transcript_id,
                "company_id": self.company['id']
            })

        await self.database.table("earnings_transcript_chunks").upsert(
            data,
            on_conflict="transcript_id,index"
        ).execute()

    def _format_segment(self, segment: List[Dict[str, str]]) -> str:
        """Formats a single segment of the transcript to a string and adds a header"""
        content = self._build_header()
        for section in segment:
            content += f"{section['speaker']}: {section['content']}\n\n"

        return content

    def _build_header(self) -> str:
        """Creates a header for embedding purposes"""
        dt = datetime.datetime.strptime(self.date, "%Y-%m-%d %H:%M:%S")

        symbols = ', '.join(self.company['symbols']) if self.company.get('symbols') else ''
        sector = self.company.get('sector', '')
        industry = self.company.get('industry', '')
        sector_industry = f"{sector} | {industry}" if sector and industry else sector or industry or ""

        return f"""# {self.company['name']} ({symbols}) | {sector_industry}
## Q{self.fiscal_quarter} FY {self.fiscal_year} Earnings Transcript Excerpt recorded on {dt.strftime('%B %-d, %Y')}

..."""
