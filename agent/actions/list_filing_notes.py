from pydantic import BaseModel, Field


class ListFilingNotes(BaseModel):
    """List the notes associated with a 10-K/10-Q/20-F filing.

    - Returns a list of ALL the notes included with a filing. Returns a unique ID, title, and a short preview for each note.
    - Use the note ID to read the full note using the 'ReadFilingNotes' tool call

    """
    filing_id: int = Field(description="The filing id for which to list the notes")
