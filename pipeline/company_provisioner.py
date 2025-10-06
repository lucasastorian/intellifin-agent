import csv
import urllib.request
import json
from pathlib import Path
from typing import List, Dict, Optional
from database.database import Database


class CompanyProvisioner:
    CSV_PATH = "./data/us_listed_stocks.csv"
    SEC_MAPPING_URL = "https://www.sec.gov/files/company_tickers_exchange.json"

    def __init__(self, database: Database, edgar_user_agent: str):
        self.database = database
        self.edgar_user_agent = edgar_user_agent

    def should_provision(self) -> bool:
        try:
            result = self.database.table("companies").select("id").limit(1).execute()
            return len(result.data) == 0
        except Exception:
            return True

    def provision(self):
        if not self.should_provision():
            return

        print("  Provisioning companies database...", end="", flush=True)

        csv_data = self._load_csv()
        sec_data = self._fetch_sec_mapping()
        records = self._merge_data(csv_data, sec_data)

        self.database.table("companies").upsert(records, on_conflict="cik").execute()

        print(f" ✓ {len(records)} companies", flush=True)

    def _load_csv(self) -> Dict[str, Dict]:
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
        req = urllib.request.Request(
            self.SEC_MAPPING_URL,
            headers={'User-Agent': self.edgar_user_agent}
        )

        with urllib.request.urlopen(req) as response:
            data = json.loads(response.read().decode('utf-8'))

        records = []
        for row in data['data']:
            records.append({
                'cik': str(row[0]).zfill(10),
                'name': row[1],
                'ticker': row[2].strip().upper(),
                'exchange': row[3],
                'sic': None,
            })

        return records

    def _merge_data(self, csv_data: Dict[str, Dict], sec_data: List[Dict]) -> List[Dict]:
        records = []

        for sec_record in sec_data:
            ticker = sec_record['ticker']
            csv_record = csv_data.get(ticker)

            if not csv_record:
                continue

            records.append({
                'name': sec_record['name'],
                'symbols': [ticker],
                'exchanges': [sec_record['exchange']],
                'cik': sec_record['cik'],
                'sic': sec_record['sic'],
                'sector': csv_record.get('sector'),
                'industry': csv_record.get('industry'),
                'market_cap': csv_record.get('market_cap'),
                'fiscal_year_end': None,
                'synced': False,
            })

        return records

    @staticmethod
    def _parse_market_cap(value: str) -> Optional[float]:
        if not value or value.lower() == 'n/a':
            return None

        cleaned = value.replace('$', '').replace(',', '').strip()

        try:
            return float(cleaned)
        except ValueError:
            return None
