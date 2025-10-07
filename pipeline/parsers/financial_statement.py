from edgar.xbrl.xbrl import XBRL

from database import Database


class FinancialStatements:

    def __init__(self, xbrl: XBRL, report_date: str, filing_id: int, company_id: int, database: Database, fiscal_period: str = None):
        self.xbrl = xbrl
        self.report_date = report_date
        self.filing_id = filing_id
        self.company_id = company_id
        self.database = database
        self.fiscal_period = fiscal_period

        self.statements = xbrl.statements

    def upsert_statements(self):
        """Upserts the statement to SQLite"""
        income_data = self._format_statement(statement=self.statements.income_statement())
        balance_sheet_data = self._format_statement(statement=self.statements.balance_sheet())
        cash_flow_data = self._format_statement(statement=self.statements.cashflow_statement())

        self.database.table("financial_statements").upsert(
            [
                {
                    "type": "income_statement",
                    "data": income_data,
                    "filing_id": self.filing_id,
                    "company_id": self.company_id
                },
                {
                    "type": "balance_sheet",
                    "data": balance_sheet_data,
                    "filing_id": self.filing_id,
                    "company_id": self.company_id
                },
                {
                    "type": "cash_flow",
                    "data": cash_flow_data,
                    "filing_id": self.filing_id,
                    "company_id": self.company_id
                }
            ],
            on_conflict="filing_id,type"
        ).execute()

    def _format_statement(self, statement):
        """Formats the statement - uses current_period_only to get only the relevant period"""
        # Use current_period_only=True to filter to only the reported period
        # This avoids YTD/comparative periods and ensures we get the right column
        df = statement.to_dataframe(include_dimensions=True, current_period_only=True)

        # Get the data columns (exclude metadata columns)
        meta_cols = ['concept', 'label', 'abstract', 'dimension', 'axis', 'member', 'period', 'level']
        data_cols = [col for col in df.columns if col not in meta_cols]

        if len(data_cols) == 0:
            raise KeyError(
                f"No data columns found in dataframe. "
                f"Available columns: {list(df.columns)}\n"
                f"fiscal_period={self.fiscal_period}, report_date={self.report_date}"
            )

        if len(data_cols) > 1:
            raise KeyError(
                f"Multiple data columns found: {data_cols}. "
                f"Expected only one with current_period_only=True.\n"
                f"fiscal_period={self.fiscal_period}, report_date={self.report_date}"
            )

        # Rename the single data column to report_date for consistency
        selected_column = data_cols[0]
        df = df[meta_cols[:-3] + [selected_column] + meta_cols[-3:]]  # Keep original column order
        df = df.rename(columns={selected_column: self.report_date})

        return df.to_dict(orient="records")
