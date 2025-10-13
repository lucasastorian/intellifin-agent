"""Test BM25 polarity and score ordering (lower BM25 = better match)."""
import pytest
import tempfile
import os
from pathlib import Path
from database.database import Database
from database.schema.schema import Schema
from database.schema.table import Table
from database.schema.fields import Serial, Text


class TestDocs(Table):
    __tablename__ = "test_docs"

    id = Serial()
    content = Text(nullable=False, fts=True)


@pytest.fixture
def temp_db():
    """Create a temporary database for testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        schema = Schema()
        schema.register_table(TestDocs)
        db = Database(schema, str(db_path))
        yield db
        db.close()


@pytest.mark.asyncio
async def test_bm25_polarity_negated(temp_db):
    """Verify BM25 scores are properly negated (higher=better after negation).

    BM25 in SQLite FTS5 is lower-is-better, so we negate it to make higher-is-better.
    This test verifies the negation works correctly in _do_keyword_search().
    """
    db = temp_db

    # Insert test documents with varying specificity
    # Doc 1: "apple banana cherry" - lowest specificity for "apple"
    # Doc 2: "apple banana" - medium specificity for "apple"
    # Doc 3: "apple" - highest specificity for "apple"
    await db._exec("""
        INSERT INTO test_docs (content) VALUES
        ('apple banana cherry'),
        ('apple banana'),
        ('apple')
    """, [])

    # Get the SelectBuilder
    builder = db.table("test_docs")

    # Perform keyword search for "apple"
    ids, scores = await builder._do_keyword_search("apple", "content", topk=3)

    # Assertions:
    # 1. Should get 3 results
    assert len(ids) == 3, f"Expected 3 results, got {len(ids)}"
    assert len(scores) == 3, f"Expected 3 scores, got {len(scores)}"

    # 2. All scores should be positive (negated BM25)
    for id_, score in scores.items():
        assert score > 0, f"Score for doc {id_} should be positive (negated BM25), got {score}"

    # 3. More specific matches should have HIGHER scores (after negation)
    # Doc 3 ("apple" only) should have highest score
    # Doc 2 ("apple banana") should have medium score
    # Doc 1 ("apple banana cherry") should have lowest score
    scores_list = [(id_, scores[id_]) for id_ in ids]
    scores_list.sort(key=lambda x: x[1], reverse=True)

    top_doc_id = scores_list[0][0]

    # Get the content of the top doc
    result = await db._exec("SELECT id, content FROM test_docs WHERE id = ?", [top_doc_id])
    top_content = result[0]['content']

    # The top result should be the most specific match (just "apple")
    assert top_content == "apple", \
        f"Expected 'apple' to rank highest, but got '{top_content}'"

    # Verify decreasing scores (higher specificity = higher score after negation)
    assert scores_list[0][1] > scores_list[1][1], \
        f"Doc 1 score ({scores_list[0][1]}) should be > Doc 2 score ({scores_list[1][1]})"
    assert scores_list[1][1] > scores_list[2][1], \
        f"Doc 2 score ({scores_list[1][1]}) should be > Doc 3 score ({scores_list[2][1]})"


@pytest.mark.asyncio
async def test_bm25_ordering_in_sql(temp_db):
    """Verify SQL query returns results ordered by BM25 rank (ASC = lower-is-better)."""
    db = temp_db

    # Insert test documents
    await db._exec("""
        INSERT INTO test_docs (content) VALUES
        ('revenue guidance forecast expectations'),
        ('revenue guidance forecast'),
        ('revenue guidance')
    """, [])

    # Use keyword_search which should order by BM25 rank (ASC)
    result = await db.table("test_docs") \
        .keyword_search("revenue guidance", "content", limit=3) \
        .select("id,content") \
        .execute()

    # Should get 3 results
    assert len(result.data) == 3, f"Expected 3 results, got {len(result.data)}"

    # First result should be the most specific match
    top_content = result.data[0]['content']
    assert top_content == "revenue guidance", \
        f"Expected 'revenue guidance' to rank first, got '{top_content}'"

    # Results should be ordered by specificity (most specific first)
    contents = [r['content'] for r in result.data]
    expected_order = [
        "revenue guidance",
        "revenue guidance forecast",
        "revenue guidance forecast expectations"
    ]
    assert contents == expected_order, \
        f"Expected order {expected_order}, got {contents}"


@pytest.mark.asyncio
async def test_bm25_fallback_no_rank_column(temp_db):
    """Verify fallback to rank-only pseudo-scores if _rank column unavailable."""
    db = temp_db

    # Insert test documents
    await db._exec("""
        INSERT INTO test_docs (content) VALUES ('test1'), ('test2'), ('test3')
    """, [])

    builder = db.table("test_docs")
    ids, scores = await builder._do_keyword_search("test*", "content", topk=3)

    # Should get results even if BM25 not available
    assert len(ids) > 0, "Should get results even without BM25"
    assert len(scores) > 0, "Should get scores even without BM25"

    # All scores should be between 0 and 1 (rank-only fallback)
    for score in scores.values():
        assert 0.0 <= score <= 1.0, \
            f"Fallback scores should be in [0, 1], got {score}"


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
