"""Test script to debug search query issue"""
import sys
from dotenv import load_dotenv
from database.database import Database
from schema import schema

load_dotenv()

# Initialize database
db = Database(schema=schema, base_path="data/.local.db")

print("=== Testing AAPL Search Query ===\n")

# First, let's check if we have any AAPL data at all
print("1. Checking if AAPL exists in companies table...")
company_result = db.table("companies").select("*").contains("symbols", "AAPL").execute()
if company_result.data:
    print(f"   ✓ Found AAPL: {company_result.data[0]['name']}")
    company_id = company_result.data[0]['id']
else:
    print("   ✗ No AAPL found in companies table")
    sys.exit(1)

# Check if we have filings for AAPL
print("\n2. Checking filings for AAPL...")
filings_result = db.table("filings").select("*").eq("company_id", company_id).execute()
print(f"   Found {len(filings_result.data)} filings")
if filings_result.data:
    for f in filings_result.data[:5]:
        print(f"   - {f['form']} | {f['filing_date']} | Report: {f['report_date']}")

# Check if we have chunks for AAPL
print("\n3. Checking filing_section_chunks for AAPL...")
chunks_result = (db.table("filing_section_chunks")
                 .select("id,section,filing_id")
                 .eq("company_id", company_id)
                 .execute())
print(f"   Found {len(chunks_result.data)} chunks")
if chunks_result.data:
    # Count by section
    section_counts = {}
    for chunk in chunks_result.data:
        section = chunk['section']
        section_counts[section] = section_counts.get(section, 0) + 1
    print("   Chunks by section:")
    for section, count in sorted(section_counts.items()):
        print(f"     - {section}: {count}")

# Now try the actual search query
print("\n4. Testing the actual search query...")
forms = ['10-K', '10-K/A', '20-F', '20-F/A']
sections = ['risk_factors', 'md&a', 'business']
start_date = '2019-10-01'
end_date = '2025-10-10'

print(f"   Query: symbol=AAPL, sections={sections}")
print(f"   Forms: {forms}")
print(f"   Date range: {start_date} → {end_date}")

# First try without vector search - just filtering
print("\n   4a. Testing filters only (no vector search)...")
query_result = (
    db.table("company_filing_section_chunks")
    .select("id,filing_id,section,form,filing_date,report_date,company_symbols")
    .contains("company_symbols", "AAPL")
    .in_("form", forms)
    .in_("section", sections)
    .gte("report_date", start_date)
    .lte("report_date", end_date)
    .execute()
)
print(f"   Found {len(query_result.data)} chunks matching filters")
if query_result.data:
    for r in query_result.data[:5]:
        print(f"     - {r['section']} | {r['form']} | {r['report_date']}")

# Now try with vector search
print("\n   4b. Testing with vector search...")
search_description = "supply chain and manufacturing risks"
try:
    vector_result = (
        db.table("company_filing_section_chunks")
        .select("id,filing_id,section,form,filing_date,report_date,company_symbols,content")
        .contains("company_symbols", "AAPL")
        .in_("form", forms)
        .in_("section", sections)
        .gte("report_date", start_date)
        .lte("report_date", end_date)
        .vector_search(search_description, "embedding", topk=15, return_scores=True)
        .execute()
    )
    print(f"   Found {len(vector_result.data)} results")
    if vector_result.data:
        for r in vector_result.data[:3]:
            score = r.get('_score', 0)
            print(f"     - {r['section']} | Score: {score:.3f}")
            # Check content structure
            content = r['content']
            if isinstance(content, list):
                print(f"       Content is list with {len(content)} pages")
            elif isinstance(content, str):
                print(f"       Content type: STRING (old format)")
                print(f"       Content preview: {content[:100]}...")
            else:
                print(f"       Content type: {type(content)}")
    else:
        print("   ✗ No vector search results")
except Exception as e:
    print(f"   ✗ Vector search failed: {e}")
    import traceback
    traceback.print_exc()

print("\n=== Test Complete ===")
