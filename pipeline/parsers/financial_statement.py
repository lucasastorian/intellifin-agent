from edgar.xbrl.xbrl import XBRL

from database import Database


class FinancialStatements:

    def __init__(self, xbrl: XBRL, report_date: str, filing_id: int, company_id: int, database: Database):
        self.xbrl = xbrl
        self.report_date = report_date
        self.filing_id = filing_id
        self.company_id = company_id
        self.database = database

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
        """Formats the statement..."""
        df = statement.to_dataframe(include_dimensions=True)

        # Find the column matching the report_date (may have fiscal period suffix like "(Q1)")
        date_column = None
        if self.report_date in df.columns:
            date_column = self.report_date
        else:
            # Search for column that starts with report_date
            matching_cols = [col for col in df.columns if str(col).startswith(self.report_date)]
            if matching_cols:
                assert len(matching_cols) == 1, (
                    f"Expected exactly one column matching report_date '{self.report_date}', "
                    f"but found {len(matching_cols)}: {matching_cols}"
                )
                date_column = matching_cols[0]  # Take the first match

        # If still not found, display error
        if date_column is None:
            print(f"\n❌ ERROR: Column '{self.report_date}' not found in dataframe!")
            print(f"\n📋 Available columns:")
            print(list(df.columns))
            print(f"\n📊 Dataframe preview:")

            # Display the dataframe if in IPython/Jupyter
            try:
                from IPython.display import display
                display(df)
            except ImportError:
                print(df)

            raise KeyError(
                f"Column '{self.report_date}' not found in financial statement dataframe. "
                f"Available columns: {list(df.columns)}"
            )

        df = df[['concept', 'label', date_column, 'abstract', 'dimension', 'axis', 'member', 'period', 'level']]
        return df.to_dict(orient="records")
