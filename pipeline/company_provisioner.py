import csv
import requests
from pathlib import Path
from typing import List, Dict, Optional, Any
from database.database import Database
from edgar import Company as EdgarCompany, set_identity


class CompanyProvisioner:

    SEC_MAPPING_URL = "https://www.sec.gov/files/company_tickers_exchange.json"

    def __init__(self, database: Database, edgar_user_agent: str, path: str = "./datasets/us_listed_stocks.csv",
                 include_csv_only: bool = False):
        self.database = database
        self.edgar_user_agent = edgar_user_agent
        self.csv_path = path
        self.include_csv_only = include_csv_only

    def provision(self, path: str = None):
        csv_data = self._load_csv()

        sec_data = self._fetch_sec_mapping()
        records = self._merge_data(csv_data, sec_data)

        print(f"\n  → Upserting {len(records)} companies to database...", flush=True)
        # await self.database.table("companies").upsert(records, on_conflict="cik").execute()

        print(f" ✓ {len(records)} companies", flush=True)

    def _load_csv(self) -> Dict[str, Dict]:
        symbol_map = {}

        csv_path = Path(self.csv_path)
        if not csv_path.exists():
            print(f"\n  ⚠ CSV not found: {self.csv_path}", flush=True)
            return {}

        with open(csv_path, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                symbol = row['Symbol'].strip().upper()

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
        print(f"\n  → Fetching SEC mapping", flush=True)
        print(f"    URL: {self.SEC_MAPPING_URL}", flush=True)
        print(f"    User-Agent: {self.edgar_user_agent}", flush=True)

        try:
            print(f"  → Making GET request...", flush=True)
            headers = {'User-Agent': self.edgar_user_agent}
            response = requests.get(self.SEC_MAPPING_URL, headers=headers, timeout=10)
            print(f"  → Received response: {response.status_code}", flush=True)
            response.raise_for_status()
            print(f"  → Parsing JSON...", flush=True)
            data = response.json()
            print(f"  → JSON parsed successfully", flush=True)
        except Exception as e:
            print(f"\n  ⚠ Error fetching SEC data: {type(e).__name__}: {e}", flush=True)
            raise

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
        cik_map = {}
        processed_tickers = set()

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

            if ticker not in cik_map[cik]['symbols']:
                cik_map[cik]['symbols'].append(ticker)
            if sec_record['exchange'] not in cik_map[cik]['exchanges']:
                cik_map[cik]['exchanges'].append(sec_record['exchange'])

        # Second pass: Add CSV-only entries (delisted companies not in SEC data)
        if self.include_csv_only:
            csv_only_tickers = [t for t in csv_data.keys() if t not in processed_tickers]
            if csv_only_tickers:
                print(f"\n  → Processing {len(csv_only_tickers)} CSV-only entries from EDGAR...", flush=True)

            processed_count = 0
            for ticker, csv_record in csv_data.items():
                if ticker in processed_tickers:
                    continue

                print(f"    Attempting {ticker}...", end="", flush=True)
                try:
                    cik, name = self._fetch_edgar_company(ticker)
                    processed_count += 1
                    print(f" ✓ [{processed_count}/{len(csv_only_tickers)}]", flush=True)

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
                except Exception as e:
                    print(f" error ({type(e).__name__})", flush=True)
                    continue
        else:
            csv_only_count = len([t for t in csv_data.keys() if t not in processed_tickers])
            if csv_only_count > 0:
                print(f"\n  → Skipped {csv_only_count} CSV-only entries (not in SEC data)", flush=True)

        return list(cik_map.values())

    def _fetch_edgar_company(self, ticker: str) -> tuple[int, Any]:
        """Blocking call to fetch company info from EDGAR - meant to be run in thread"""
        set_identity(self.edgar_user_agent)
        edgar_company = EdgarCompany(ticker)
        return edgar_company.cik, edgar_company.name

    @staticmethod
    def _parse_market_cap(value: str) -> Optional[float]:
        if not value or value.lower() == 'n/a':
            return None

        cleaned = value.replace('$', '').replace(',', '').strip()

        try:
            return float(cleaned)
        except ValueError:
            return None
