import csv
import urllib.request
import json
from pathlib import Path
from typing import List, Dict, Optional
from database.database import Database


class CompanyProvisioner:
    """Provisions companies table with US listed stocks data"""

    CSV_PATH = "./data/us_listed_stocks.csv"
    SEC_MAPPING_URL = "https://www.sec.gov/files/company_tickers_exchange.json"

    def __init__(self, database: Database, edgar_user_agent: str):
        self.database = database
        self.edgar_user_agent = edgar_user_agent

    def should_provision(self) -> bool:
        """Check if companies table is empty"""
        try:
            result = self.database.table("companies").select("id").limit(1).execute()
            return len(result) == 0
        except Exception:
            return True

    def provision(self):
        """Provision all US listed companies from CSV + SEC mapping"""
        if not self.should_provision():
            return

        print("  Provisioning companies database...", end="", flush=True)

        # Load data sources
        csv_data = self._load_csv()
        sec_data = self._fetch_sec_mapping()

        # Merge and build records
        records = self._merge_data(csv_data, sec_data)

        # Single bulk upsert
        self.database.table("companies").upsert(records, on_conflict="cik").execute()

        print(f" ✓ {len(records)} companies", flush=True)

    def _load_csv(self) -> Dict[str, Dict]:
        """Load CSV and index by symbol"""
        symbol_map = {}

        csv_path = Path(self.CSV_PATH)
        if not csv_path.exists():
            print(f"\n  ⚠ CSV not found: {self.CSV_PATH}", flush=True)
            return {}

        with open(csv_path, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                symbol = row['Symbol'].strip().upper()
                symbol_map[symbol] = {
                    'sector': row.get('Sector', '').strip() or None,
                    'industry': row.get('Industry', '').strip() or None,
                    'market_cap': self._parse_market_cap(row.get('Market Cap', '')),
                }

        return symbol_map

    def _fetch_sec_mapping(self) -> List[Dict]:
        """Fetch SEC ticker to CIK mapping"""
        req = urllib.request.Request(
            self.SEC_MAPPING_URL,
            headers={'User-Agent': self.edgar_user_agent}
        )

        with urllib.request.urlopen(req) as response:
            data = json.loads(response.read().decode('utf-8'))

        # Convert to list of dicts
        records = []
        for row in data['data']:
            records.append({
                'cik': str(row[0]).zfill(10),  # Pad CIK to 10 digits
                'name': row[1],
                'ticker': row[2].strip().upper(),
                'exchange': row[3],
            })

        return records

    def _merge_data(self, csv_data: Dict[str, Dict], sec_data: List[Dict]) -> List[Dict]:
        """Merge CSV and SEC data, build company records"""
        records = []

        for sec_record in sec_data:
            ticker = sec_record['ticker']
            csv_record = csv_data.get(ticker, {})

            records.append({
                'name': sec_record['name'],
                'symbols': [ticker],
                'exchanges': [sec_record['exchange']],
                'cik': sec_record['cik'],
                'sic': '',  # Placeholder, will be updated on first sync
                'sector': csv_record.get('sector'),
                'industry': csv_record.get('industry'),
                'market_cap': csv_record.get('market_cap'),
                'fiscal_year_end': None,
                'synced': False,
            })

        return records

    @staticmethod
    def _parse_market_cap(value: str) -> Optional[float]:
        """Parse market cap string to float"""
        if not value or value.lower() == 'n/a':
            return None

        # Remove dollar signs and commas
        cleaned = value.replace('$', '').replace(',', '').strip()

        try:
            return float(cleaned)
        except ValueError:
            return None
