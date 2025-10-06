#!/usr/bin/env python
"""Test script to reproduce the search issue"""

from datetime import date
from schema import schema
from database import Database

# Initialize database
db = Database(schema=schema, base_path="./data/.local.db")

# Get company for symbol X
companies = db.table("companies").select("id,name,symbols").contains("symbols", "X").execute()
print(f"Companies found: {companies}")

if not companies:
    print("No company found for symbol X!")
    exit(1)

company_id = companies[0]['id']
print(f"\nCompany ID: {company_id}")

# Check all filings for this company
all_filings = db.table("filings").select("*").eq("company_id", company_id).execute()
print(f"\nTotal filings for company_id {company_id}: {len(all_filings)}")

if all_filings:
    print("\nFirst 5 filings:")
    for f in all_filings[:5]:
        print(f"  {f['form']} - filing_date: {f['filing_date']}, report_date: {f.get('report_date')}")

# Test the exact query from SearchFilings
forms = ["8-K", "8-K/A", "10-K", "10-K/A", "10-Q", "10-Q/A"]
start_date = "2025-01-01"
end_date = date.today().isoformat()

print(f"\n\nTesting query with:")
print(f"  company_id: {company_id}")
print(f"  forms: {forms}")
print(f"  filing_date range: {start_date} → {end_date}")

filings = (
    db.table("filings")
    .select("id,form,filing_date,report_date")
    .in_("company_id", [company_id])
    .in_("form", forms)
    .gte("filing_date", start_date)
    .lte("filing_date", end_date)
    .order("filing_date", desc=True)
    .limit(50)
    .execute()
)

print(f"\nFilings found: {len(filings)}")
if filings:
    print("\nResults:")
    for f in filings:
        print(f"  {f['id']} - {f['form']} - {f['filing_date']}")
