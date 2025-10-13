from edgar.xbrl.xbrl import XBRL

from database import Database


class FinancialStatements:

    def __init__(self, xbrl: XBRL, report_date: str, filing_id: int, company_id: int, database: Database,
                 fiscal_period: str = None, form: str = None, accession_number: str = None):
        self.xbrl = xbrl
        self.report_date = report_date
        self.filing_id = filing_id
        self.company_id = company_id
        self.database = database
        self.fiscal_period = fiscal_period
        self.form = form
        self.accession_number = accession_number

        self.statements = xbrl.statements

    async def upsert_statements(self):
        """Upserts the statement to SQLite"""
        income_data = self._format_statement(statement=self.statements.income_statement(), statement_type="income_statement")
        balance_sheet_data = self._format_statement(statement=self.statements.balance_sheet(), statement_type="balance_sheet")
        cash_flow_data = self._format_statement(statement=self.statements.cashflow_statement(), statement_type="cash_flow")

        # Only upsert statements that have data
        statements_to_upsert = []
        if income_data:
            statements_to_upsert.append({
                "type": "income_statement",
                "data": income_data,
                "filing_id": self.filing_id,
                "company_id": self.company_id
            })
        if balance_sheet_data:
            statements_to_upsert.append({
                "type": "balance_sheet",
                "data": balance_sheet_data,
                "filing_id": self.filing_id,
                "company_id": self.company_id
            })
        if cash_flow_data:
            statements_to_upsert.append({
                "type": "cash_flow",
                "data": cash_flow_data,
                "filing_id": self.filing_id,
                "company_id": self.company_id
            })

        if statements_to_upsert:
            await self.database.table("financial_statements").upsert(
                statements_to_upsert,
                on_conflict="filing_id,type"
            ).execute()

    def _format_statement(self, statement, statement_type: str = "unknown"):
        """Formats the statement - uses current_period_only to get only the relevant period"""
        # Handle missing statements (e.g., bankruptcy filings, incomplete XBRL)
        if statement is None:
            print(
                f"  ⚠ Warning: {statement_type} is None for {self.form or 'unknown'} "
                f"(filing_id={self.filing_id}, accession={self.accession_number})",
                flush=True
            )
            return []

        # Use current_period_only=True to filter to only the reported period
        # This avoids YTD/comparative periods and ensures we get the right column
        df = statement.to_dataframe(include_dimensions=True, current_period_only=True)

        # Get the data columns (exclude metadata columns)
        meta_cols = ['concept', 'label', 'abstract', 'dimension', 'axis', 'member', 'period', 'level']
        data_cols = [col for col in df.columns if col not in meta_cols]

        if len(data_cols) == 0:
            # Normal for amendments - just log a warning and skip
            print(
                f"  ⚠ Warning: No data columns in {statement_type} for {self.form or 'unknown'} "
                f"(filing_id={self.filing_id}, accession={self.accession_number}). "
                f"Normal for amendments. fiscal_period={self.fiscal_period}, report_date={self.report_date}",
                flush=True
            )
            return []

        if len(data_cols) > 1:
            # Log warning and use the first column
            print(
                f"  ⚠ Warning: Multiple data columns in {statement_type} for {self.form or 'unknown'}: {data_cols}. "
                f"Using first column. "
                f"(filing_id={self.filing_id}, accession={self.accession_number}, "
                f"fiscal_period={self.fiscal_period}, report_date={self.report_date})",
                flush=True
            )
            selected_column = data_cols[0]
        else:
            selected_column = data_cols[0]

        # Rename the single data column to report_date for consistency
        df = df[meta_cols[:-3] + [selected_column] + meta_cols[-3:]]  # Keep original column order
        df = df.rename(columns={selected_column: self.report_date})

        return df.to_dict(orient="records")
