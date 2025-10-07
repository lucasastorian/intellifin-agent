import csv
import urllib.request
import json
from pathlib import Path
from typing import List, Dict, Optional
from database.database import Database
from edgar import Company as EdgarCompany, set_identity


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
                # Parse delisted field (case-insensitive, handles True/true/TRUE)
                delisted_str = row.get('Delisted', 'False').strip().lower()
                delisted = delisted_str in ('true', '1', 'yes')

                symbol_map[symbol] = {
                    'sector': row.get('Sector', '').strip() or None,
                    'industry': row.get('Industry', '').strip() or None,
                    'market_cap': self._parse_market_cap(row.get('Market Cap', '')),
                    'country': row.get('Country', '').strip() or None,
                    'delisted': delisted,
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
        # Group SEC records by CIK to aggregate all tickers for the same company
        cik_map = {}
        processed_tickers = set()

        # First pass: Process all SEC records and merge with CSV data
        for sec_record in sec_data:
            ticker = sec_record['ticker']
            csv_record = csv_data.get(ticker)

            if not csv_record:
                continue

            processed_tickers.add(ticker)

            cik = sec_record['cik']
            if cik not in cik_map:
                cik_map[cik] = {
                    'name': sec_record['name'],
                    'symbols': [],
                    'exchanges': [],
                    'cik': cik,
                    'sic': sec_record['sic'],
                    'sector': csv_record.get('sector'),
                    'industry': csv_record.get('industry'),
                    'market_cap': csv_record.get('market_cap'),
                    'country': csv_record.get('country'),
                    'delisted': csv_record.get('delisted', False),
                    'fiscal_year_end': None,
                    'synced': False,
                }

            # Add ticker and exchange if not already present
            if ticker not in cik_map[cik]['symbols']:
                cik_map[cik]['symbols'].append(ticker)
            if sec_record['exchange'] not in cik_map[cik]['exchanges']:
                cik_map[cik]['exchanges'].append(sec_record['exchange'])

        # Second pass: Add CSV-only entries (delisted companies not in SEC data)
        # Fetch these individually from EDGAR using EdgarCompany
        for ticker, csv_record in csv_data.items():
            if ticker in processed_tickers:
                continue

            # Try to fetch company info from EDGAR for delisted companies
            try:
                set_identity(self.edgar_user_agent)
                edgar_company = EdgarCompany(ticker)
                cik = edgar_company.cik
                name = edgar_company.name if hasattr(edgar_company, 'name') else ticker

                cik_map[cik] = {
                    'name': name,
                    'symbols': [ticker],
                    'exchanges': [],
                    'cik': cik,
                    'sic': None,
                    'sector': csv_record.get('sector'),
                    'industry': csv_record.get('industry'),
                    'market_cap': csv_record.get('market_cap'),
                    'country': csv_record.get('country'),
                    'delisted': csv_record.get('delisted', False),
                    'fiscal_year_end': None,
                    'synced': False,
                }
            except Exception:
                # Skip companies that can't be found on EDGAR
                continue

        return list(cik_map.values())

    @staticmethod
    def _parse_market_cap(value: str) -> Optional[float]:
        if not value or value.lower() == 'n/a':
            return None

        cleaned = value.replace('$', '').replace(',', '').strip()

        try:
            return float(cleaned)
        except ValueError:
            return None
