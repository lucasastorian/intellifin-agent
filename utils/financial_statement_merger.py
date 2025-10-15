from typing import Literal, Optional, List
import pandas as pd
from datetime import datetime
import re

# Regex to detect date range columns like "YYYY-MM-DD - YYYY-MM-DD"
DATE_COL_RE = re.compile(r"\d{4}-\d{2}-\d{2}\s*-\s*\d{4}-\d{2}-\d{2}")


class FinancialStatementMerger:
    """Merges financial statements across periods with Q4 inference for quarterly data"""

    def __init__(
            self,
            statements: list,
            report_type: Literal['annual', 'quarterly'],
            include_segments: bool = False,
            requested_start_date: Optional[str] = None,
            requested_end_date: Optional[str] = None
    ):
        self.statements = statements
        self.report_type = report_type
        self.include_segments = include_segments
        self.requested_start_date = requested_start_date
        self.requested_end_date = requested_end_date

    @staticmethod
    def _first_value_col(cols: List[str]) -> Optional[str]:
        """Select first valid date-like string column from list.

        Prefers columns matching date range pattern, falls back to first string column.
        """
        # Prefer string columns that look like date ranges
        for c in cols:
            if isinstance(c, str) and DATE_COL_RE.search(c):
                return c
        # Fall back to first string column
        for c in cols:
            if isinstance(c, str):
                return c
        # Last resort: stringify first col
        return str(cols[0]) if cols else None

    @staticmethod
    def _normalize_dataframe_columns(df: pd.DataFrame) -> pd.DataFrame:
        """Ensure required columns exist with proper defaults."""
        defaults = {
            'axis': '',
            'member': '',
            'level': 0,
            'abstract': False,
            'dimension': False,
            'period': ''
        }
        for col, default in defaults.items():
            if col not in df.columns:
                df[col] = default
            else:
                df[col] = df[col].fillna(default)
        return df

    def merge(self) -> pd.DataFrame:
        """Main entry point - merges statements based on report type"""
        if self.report_type == 'annual':
            return self._merge_annual()
        else:
            return self._merge_quarterly_with_q4_inference()

    def _merge_annual(self) -> pd.DataFrame:
        """Simple merge for annual statements (no Q4 inference needed)"""
        # Sort by report_date
        statements = sorted(self.statements, key=lambda x: x['report_date'])
        return self._merge_statements(statements)

    def _merge_quarterly_with_q4_inference(self) -> pd.DataFrame:
        """Merge quarterly statements with Q4 inference from annual data"""
        # Separate annual and quarterly statements
        annual_stmts = [s for s in self.statements if s.get('fiscal_period') == 'FY']
        quarterly_stmts = [s for s in self.statements if s.get('fiscal_period') in ['Q1', 'Q2', 'Q3']]

        # Normalize YTD to quarterly values
        quarterly_stmts = self._normalize_ytd_to_quarterly(quarterly_stmts)

        # Group by fiscal year
        annual_by_year = {s['fiscal_year']: s for s in annual_stmts}
        quarterly_by_year = {}
        for stmt in quarterly_stmts:
            year = stmt['fiscal_year']
            if year not in quarterly_by_year:
                quarterly_by_year[year] = {}
            quarterly_by_year[year][stmt['fiscal_period']] = stmt

        # Infer Q4 for each fiscal year
        inferred_q4_stmts = []
        for year, annual_stmt in annual_by_year.items():
            if year in quarterly_by_year:
                quarters = quarterly_by_year[year]
                # Only infer if we have Q1, Q2, Q3
                if all(q in quarters for q in ['Q1', 'Q2', 'Q3']):
                    q4_stmt = self._infer_q4(
                        annual_stmt=annual_stmt,
                        q1_stmt=quarters['Q1'],
                        q2_stmt=quarters['Q2'],
                        q3_stmt=quarters['Q3']
                    )
                    if q4_stmt:
                        inferred_q4_stmts.append(q4_stmt)

        # Combine all quarterly statements (Q1, Q2, Q3, inferred Q4)
        all_quarterly = quarterly_stmts + inferred_q4_stmts

        # Filter to requested date range if specified
        if self.requested_start_date or self.requested_end_date:
            all_quarterly = [
                s for s in all_quarterly
                if (not self.requested_start_date or s['report_date'] >= self.requested_start_date) and
                   (not self.requested_end_date or s['report_date'] <= self.requested_end_date)
            ]

        # Sort by report_date
        all_quarterly = sorted(all_quarterly, key=lambda x: x['report_date'])

        return self._merge_statements(all_quarterly)

    def _normalize_ytd_to_quarterly(self, quarterly_stmts: list) -> list:
        """Normalize YTD values to quarterly through date-driven detection and subtraction

        Uses start/end dates from column names to detect YTD vs quarterly periods.
        YTD detection: start_date matches fiscal year start
        """
        # Group by fiscal year
        by_year = {}
        for stmt in quarterly_stmts:
            fy = stmt['fiscal_year']
            fp = stmt['fiscal_period']
            if fy not in by_year:
                by_year[fy] = {}
            by_year[fy][fp] = stmt

        normalized = []

        for fy, quarters in by_year.items():
            # Get fiscal year start from first statement (should be consistent)
            first_stmt = next(iter(quarters.values()))
            fy_start = self._get_fiscal_year_start(first_stmt)

            for period in ['Q1', 'Q2', 'Q3']:
                if period not in quarters:
                    continue

                stmt = quarters[period]
                df = pd.DataFrame(stmt['data'])
                df = self._normalize_dataframe_columns(df)

                # Get the value column
                meta_cols = ['concept', 'label', 'abstract', 'dimension', 'axis', 'member', 'period', 'level']
                value_cols = [col for col in df.columns if col not in meta_cols]

                if not value_cols:
                    normalized.append(stmt)
                    continue

                value_col = self._first_value_col(value_cols)
                if not value_col:
                    normalized.append(stmt)
                    continue

                # Parse start date from column name
                start_date = self._parse_start_date_from_column(value_col)

                # YTD detection: start_date matches fiscal year start
                is_ytd = start_date and fy_start and start_date == fy_start

                if not is_ytd:
                    # Already quarterly, use as-is
                    normalized.append(stmt)
                    continue

                # Normalize YTD to quarterly through subtraction
                if period == 'Q1':
                    # Q1 YTD = Q1
                    normalized.append(stmt)
                elif period == 'Q2' and 'Q1' in quarters:
                    # Q2 = Q2_YTD - Q1
                    q1_stmt = quarters['Q1']
                    normalized_stmt = self._subtract_statements(stmt, q1_stmt, period)
                    normalized.append(normalized_stmt)
                elif period == 'Q3' and 'Q2' in quarters:
                    # Q3 = Q3_YTD - Q2_normalized
                    q2_stmt = next(
                        (s for s in normalized if s['fiscal_year'] == fy and s['fiscal_period'] == 'Q2'),
                        quarters.get('Q2')
                    )
                    if q2_stmt:
                        normalized_stmt = self._subtract_statements(stmt, q2_stmt, period)
                        normalized.append(normalized_stmt)
                    else:
                        # Missing Q2, can't normalize - leave as YTD
                        normalized.append(stmt)
                else:
                    # Can't normalize without prior quarter
                    normalized.append(stmt)

        return normalized

    def _get_fiscal_year_start(self, stmt: dict) -> Optional[datetime]:
        """Infer fiscal year start from Q1 statement data

        For Q1, the start date in the column name IS the fiscal year start.
        Example: Apple Q1 column "2024-09-29 - 2024-12-28 (Q1 2025)" → FY start is 2024-09-29
        """
        if stmt.get('fiscal_period') == 'Q1':
            df = pd.DataFrame(stmt['data'])
            meta_cols = ['concept', 'label', 'abstract', 'dimension', 'axis', 'member', 'period', 'level']
            value_cols = [col for col in df.columns if col not in meta_cols]

            if value_cols:
                value_col = self._first_value_col(value_cols)
                if value_col:
                    return self._parse_start_date_from_column(value_col)

        return None

    def _parse_start_date_from_column(self, column_name) -> Optional[datetime]:
        """Parse start date from column name like '2024-09-29 - 2025-06-28 (Q3 2025)'"""
        if not isinstance(column_name, str):
            return None
        try:
            if ' - ' in column_name:
                date_part = column_name.split('(')[0].strip() if '(' in column_name else column_name
                start_str = date_part.split(' - ')[0].strip()
                return datetime.strptime(start_str, '%Y-%m-%d')
        except Exception:
            pass
        return None

    def _subtract_statements(self, ytd_stmt: dict, prior_stmt: dict, period: str) -> dict:
        """Subtract prior statement from YTD statement to get quarterly values"""
        ytd_df = pd.DataFrame(ytd_stmt['data'])
        prior_df = pd.DataFrame(prior_stmt['data'])

        # Normalize columns first
        ytd_df = self._normalize_dataframe_columns(ytd_df)
        prior_df = self._normalize_dataframe_columns(prior_df)

        # Filter by include_segments
        if not self.include_segments:
            ytd_df = ytd_df[ytd_df['dimension'] == False].copy()
            prior_df = prior_df[prior_df['dimension'] == False].copy()

        # Create merge keys
        ytd_df['_merge_key'] = ytd_df.apply(
            lambda row: (row['concept'], row.get('dimension', False), row.get('axis', ''), row.get('member', '')),
            axis=1
        )
        prior_df['_merge_key'] = prior_df.apply(
            lambda row: (row['concept'], row.get('dimension', False), row.get('axis', ''), row.get('member', '')),
            axis=1
        )

        # Get value columns
        meta_cols = ['concept', 'label', 'abstract', 'dimension', 'axis', 'member', 'period', 'level', '_merge_key']
        ytd_value_cols = [col for col in ytd_df.columns if col not in meta_cols]
        prior_value_cols = [col for col in prior_df.columns if col not in meta_cols]

        ytd_value_col = self._first_value_col(ytd_value_cols)
        prior_value_col = self._first_value_col(prior_value_cols)

        if not ytd_value_col or not prior_value_col:
            # Can't subtract without value columns
            return ytd_stmt

        # Merge
        merged = ytd_df.merge(
            prior_df[['_merge_key', prior_value_col]],
            on='_merge_key',
            how='left'
        )

        # Calculate quarterly value
        def calc_quarterly_value(row):
            ytd_val = row[ytd_value_col]
            prior_val = row.get(prior_value_col)

            # Only subtract for duration items (P&L, Cash Flow)
            if row.get('period') == 'duration':
                if pd.notna(ytd_val) and pd.notna(prior_val):
                    return ytd_val - prior_val
                # If no prior value, return NaN (can't normalize)
                return None
            else:
                # For instant items (Balance Sheet), use YTD value
                return ytd_val

        # Create new column name
        new_col_name = f"{ytd_stmt['fiscal_year']} {period}"

        merged[new_col_name] = merged.apply(calc_quarterly_value, axis=1)

        # Build normalized data
        normalized_data = []
        for _, row in merged.iterrows():
            normalized_data.append({
                'concept': row['concept'],
                'label': row['label'],
                new_col_name: row[new_col_name],
                'abstract': row.get('abstract', False),
                'dimension': row.get('dimension', False),
                'axis': row.get('axis', ''),
                'member': row.get('member', ''),
                'period': row.get('period', ''),
                'level': row.get('level', 0)
            })

        return {
            **ytd_stmt,
            'data': normalized_data
        }

    def _infer_q4(self, annual_stmt: dict, q1_stmt: dict, q2_stmt: dict, q3_stmt: dict) -> Optional[dict]:
        """Infer Q4 statement from Annual - Q1 - Q2 - Q3

        Only infers if ALL four statements exist and share the same fiscal year.
        Skips any line items where values are missing.
        """
        try:
            # Validate fiscal years match
            fy = annual_stmt['fiscal_year']
            if not all(stmt['fiscal_year'] == fy for stmt in [q1_stmt, q2_stmt, q3_stmt]):
                return None

            # Convert statements to DataFrames
            annual_df = pd.DataFrame(annual_stmt['data'])
            q1_df = pd.DataFrame(q1_stmt['data'])
            q2_df = pd.DataFrame(q2_stmt['data'])
            q3_df = pd.DataFrame(q3_stmt['data'])

            # Normalize columns first
            annual_df = self._normalize_dataframe_columns(annual_df)
            q1_df = self._normalize_dataframe_columns(q1_df)
            q2_df = self._normalize_dataframe_columns(q2_df)
            q3_df = self._normalize_dataframe_columns(q3_df)

            # Filter by include_segments
            if not self.include_segments:
                annual_df = annual_df[annual_df['dimension'] == False].copy()
                q1_df = q1_df[q1_df['dimension'] == False].copy()
                q2_df = q2_df[q2_df['dimension'] == False].copy()
                q3_df = q3_df[q3_df['dimension'] == False].copy()

            # Create merge keys for each dataframe
            for df in [annual_df, q1_df, q2_df, q3_df]:
                df['_merge_key'] = df.apply(
                    lambda row: (
                        row['concept'],
                        row.get('dimension', False),
                        row.get('axis', ''),
                        row.get('member', '')
                    ),
                    axis=1
                )

            # Find value columns (exclude metadata columns)
            meta_cols = ['concept', 'label', 'abstract', 'dimension', 'axis', 'member', 'period', 'level', '_merge_key']

            annual_value_cols = [col for col in annual_df.columns if col not in meta_cols]
            q1_value_cols = [col for col in q1_df.columns if col not in meta_cols]
            q2_value_cols = [col for col in q2_df.columns if col not in meta_cols]
            q3_value_cols = [col for col in q3_df.columns if col not in meta_cols]

            annual_value_col = self._first_value_col(annual_value_cols)
            q1_value_col = self._first_value_col(q1_value_cols)
            q2_value_col = self._first_value_col(q2_value_cols)
            q3_value_col = self._first_value_col(q3_value_cols)

            if not all([annual_value_col, q1_value_col, q2_value_col, q3_value_col]):
                # Missing value columns, can't infer Q4
                return None

            # Merge all dataframes on merge key
            merged = annual_df[[
                '_merge_key', 'concept', 'label', 'level', 'dimension', 'axis', 'member', 'period', 'abstract', annual_value_col
            ]].copy()

            merged = merged.merge(
                q1_df[['_merge_key', q1_value_col]],
                on='_merge_key',
                how='left'
            )
            merged = merged.merge(
                q2_df[['_merge_key', q2_value_col]],
                on='_merge_key',
                how='left'
            )
            merged = merged.merge(
                q3_df[['_merge_key', q3_value_col]],
                on='_merge_key',
                how='left'
            )

            # Calculate Q4 values
            def calc_q4_value(row):
                period = row.get('period', '')
                annual_val = row[annual_value_col]
                q1_val = row[q1_value_col]
                q2_val = row[q2_value_col]
                q3_val = row[q3_value_col]

                # For instant (balance sheet items), use annual value as Q4
                if period == 'instant':
                    return annual_val

                # For duration (income statement, cash flow), calculate Q4 = Annual - Q1 - Q2 - Q3
                elif period == 'duration':
                    # Skip if ANY value is missing
                    if pd.isna(annual_val) or pd.isna(q1_val) or pd.isna(q2_val) or pd.isna(q3_val):
                        return None

                    return annual_val - q1_val - q2_val - q3_val

                # For abstract or other types, return None
                return None

            merged['q4_value'] = merged.apply(calc_q4_value, axis=1)

            # Build Q4 statement data
            q4_data = []
            q4_col_name = f"{fy} Q4"

            for _, row in merged.iterrows():
                q4_data.append({
                    'concept': row['concept'],
                    'label': row['label'],
                    'level': row.get('level', 0),
                    'dimension': row.get('dimension', False),
                    'axis': row.get('axis', ''),
                    'member': row.get('member', ''),
                    'period': row.get('period', ''),
                    'abstract': row.get('abstract', False),
                    q4_col_name: row['q4_value']
                })

            q4_stmt = {
                'fiscal_year': fy,
                'fiscal_period': 'Q4',
                'report_date': annual_stmt['report_date'],
                'form': '10-K (Q4 inferred)',
                'data': q4_data
            }

            return q4_stmt

        except Exception as e:
            print(f"Warning: Failed to infer Q4 for fiscal year {annual_stmt.get('fiscal_year')}: {e}")
            return None

    def _merge_statements(self, statements: list) -> pd.DataFrame:
        """Merge multiple statements by (concept, dimension, axis, member), preserving order"""
        if not statements:
            return pd.DataFrame()

        dfs = []
        period_labels = []

        for stmt in statements:
            data = stmt['data']
            df = pd.DataFrame(data)
            df = self._normalize_dataframe_columns(df)

            if not self.include_segments:
                df = df[df['dimension'] == False].copy()

            df['_merge_key'] = df.apply(
                lambda row: (row['concept'], row['dimension'], row.get('axis', ''), row.get('member', '')),
                axis=1
            )

            fiscal_year = stmt.get('fiscal_year', '')
            fiscal_period = stmt.get('fiscal_period', '')
            period_label = f"{fiscal_year} {fiscal_period}" if fiscal_year and fiscal_period else stmt['report_date']

            date_cols = [col for col in df.columns if
                         col not in ['concept', 'label', 'abstract', 'dimension', 'axis', 'member', 'period', 'level',
                                     '_merge_key']]
            if date_cols:
                value_col = self._first_value_col(date_cols)
                if value_col:
                    df = df[['_merge_key', 'concept', 'label', 'level', 'dimension', 'axis', 'member', value_col]].copy()
                    df.rename(columns={value_col: period_label}, inplace=True)
                    dfs.append(df)
                    period_labels.append(period_label)

        if not dfs:
            return pd.DataFrame()

        merge_key_order = []
        for df in dfs:
            for key in df['_merge_key'].values:
                if key not in merge_key_order:
                    merge_key_order.append(key)

        # Keep member column in merge (was missing, could cause row collapse)
        merged = dfs[0][['_merge_key', 'concept', 'label', 'level', 'dimension', 'axis', 'member']].copy()
        for i, df in enumerate(dfs):
            period_col = period_labels[i]

            # Merge with full metadata to avoid NaN pollution when new concepts appear
            merged = merged.merge(
                df[['_merge_key', 'concept', 'label', 'level', 'dimension', 'axis', 'member', period_col]],
                on='_merge_key',
                how='outer',
                suffixes=('', '_new')
            )

            # Coalesce metadata: use original values, fill missing with new values
            for col in ['concept', 'label', 'level', 'dimension', 'axis', 'member']:
                if f'{col}_new' in merged.columns:
                    # Use where to coalesce without triggering concat-with-empty deprecation
                    # Keep explicit infer_objects for stable dtype behavior
                    merged[col] = merged[col].where(merged[col].notna(), merged[f'{col}_new']).infer_objects(copy=False)
                    merged.drop(columns=[f'{col}_new'], inplace=True)

        merged['_order'] = merged['_merge_key'].map({k: i for i, k in enumerate(merge_key_order)})
        merged = merged.sort_values('_order').drop(columns=['_order', '_merge_key']).reset_index(drop=True)

        return merged
