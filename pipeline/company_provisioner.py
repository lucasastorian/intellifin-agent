import asyncio

import aiohttp
import pandas as pd
from pathlib import Path
from typing import List, Dict

from database.database import Database


class CompanyProvisioner:
    SEC_TICKER_URL = "https://www.sec.gov/include/ticker.txt"
    SEC_EXCHANGE_URL = "https://www.sec.gov/files/company_tickers_exchange.json"

    def __init__(self, database: Database, edgar_user_agent: str, path: str = "./datasets/us_listed_stocks.csv"):
        self.database = database
        self.edgar_user_agent = edgar_user_agent
        self.csv_path = path

    async def provision(self):
        csv_data = self._load_csv()
        ticker_to_cik, exchange_data = await asyncio.gather(
            self._fetch_ticker_cik_mapping(),
            self._fetch_exchange_mapping()
        )

        records = self._merge_data(csv_data, ticker_to_cik, exchange_data)

        print(f"\n  → Upserting {len(records)} companies to database...", flush=True)
        await self.database.table("companies").upsert(records, on_conflict="cik").execute()

        await asyncio.sleep(1)

        print(f" ✓ {len(records)} companies", flush=True)

    @staticmethod
    def _merge_data(csv_data: Dict[str, Dict], ticker_to_cik: Dict[str, str], exchange_data: Dict[str, Dict]) -> List[Dict]:
        cik_map = {}

        # Process all tickers from CSV
        for ticker, csv_record in csv_data.items():
            # Try exchange JSON first (has more data for currently listed), then fall back to ticker.txt
            exchange_info = exchange_data.get(ticker)
            cik = exchange_info['cik'] if exchange_info else ticker_to_cik.get(ticker.lower())

            if not cik:
                continue

            # Initialize company record if this is the first time we see this CIK
            if cik not in cik_map:
                cik_map[cik] = {
                    'name': exchange_info['name'] if exchange_info else csv_record['name'],
                    'symbols': [],
                    'exchanges': [],
                    'cik': cik,
                    'sic': exchange_info['sic'] if exchange_info else None,
                    'sector': csv_record.get('sector'),
                    'industry': csv_record.get('industry'),
                    'market_cap': csv_record.get('market_cap'),
                    'country': csv_record.get('country'),
                    'delisted': csv_record.get('delisted', False),
                    'fiscal_year_end': None,
                    'synced': False,
                }

            # Add this ticker to the symbols list (handles multiple symbols for same CIK)
            if ticker not in cik_map[cik]['symbols']:
                cik_map[cik]['symbols'].append(ticker)

            # Add exchange if available
            if exchange_info and exchange_info['exchange'] not in cik_map[cik]['exchanges']:
                cik_map[cik]['exchanges'].append(exchange_info['exchange'])

        return list(cik_map.values())

    def _load_csv(self) -> Dict[str, Dict]:
        """Load the CSV"""
        csv_path = Path(self.csv_path)
        if not csv_path.exists():
            print(f"\n  ⚠ CSV not found: {self.csv_path}", flush=True)
            return {}

        df = pd.read_csv(csv_path)

        df["Symbol"] = df["Symbol"].astype(str).str.strip().str.upper()
        # Opt-in to future behavior (no silent downcast) for fillna, then cast explicitly
        with pd.option_context('future.no_silent_downcasting', True):
            df["Delisted"] = df["Delisted"].fillna(False)
        df["Delisted"] = df["Delisted"].astype(bool)
        df["Market Cap"] = pd.to_numeric(df["Market Cap"], errors='coerce')

        for col in ["Sector", "Industry", "Country"]:
            if col in df.columns:
                df[col] = df[col].replace("", None)

        # Drop duplicate symbols, keeping first occurrence
        df = df.drop_duplicates(subset=["Symbol"], keep="first")

        symbol_map = (
            df.set_index("Symbol")[["Name", "Sector", "Industry", "Market Cap", "Country", "Delisted"]]
            .rename(columns={
                "Name": "name",
                "Sector": "sector",
                "Industry": "industry",
                "Market Cap": "market_cap",
                "Country": "country",
                "Delisted": "delisted",
            })
            .to_dict(orient="index")
        )

        return symbol_map

    async def _fetch_ticker_cik_mapping(self) -> Dict[str, str]:
        """Fetch ticker->CIK mapping from SEC ticker.txt"""
        async with aiohttp.ClientSession() as session:
            async with session.get(self.SEC_TICKER_URL, headers={"User-Agent": self.edgar_user_agent}) as response:
                response.raise_for_status()
                text = await response.text()

                ticker_to_cik = {}
                for line in text.strip().split('\n'):
                    parts = line.split('\t')
                    if len(parts) == 2:
                        ticker, cik = parts
                        ticker_to_cik[ticker.strip().lower()] = str(cik.strip()).zfill(10)

                return ticker_to_cik

    async def _fetch_exchange_mapping(self) -> Dict[str, Dict]:
        """Fetch CIK, exchange and SIC info for currently listed companies"""
        async with aiohttp.ClientSession() as session:
            async with session.get(self.SEC_EXCHANGE_URL, headers={"User-Agent": self.edgar_user_agent}) as response:
                response.raise_for_status()
                data = await response.json()

                exchange_map = {}
                for row in data['data']:
                    ticker = row[2].strip().upper()
                    exchange_map[ticker] = {
                        'cik': str(row[0]).zfill(10),
                        'name': row[1],
                        'exchange': row[3],
                        'sic': row[4] if len(row) > 4 else None,
                    }

                return exchange_map
