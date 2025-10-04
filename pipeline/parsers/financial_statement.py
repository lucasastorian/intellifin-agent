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
                "type": "cash_flow_statement",
                "data": cash_flow_data,
                "filing_id": self.filing_id,
                "company_id": self.company_id
            }
        )

    def _format_statement(self, statement):
        """Formats the statement..."""
        df = statement.to_dataframe(include_dimensions=True)
        df = df[['concept', 'label', self.report_date, 'abstract', 'dimension', 'axis', 'member', 'period', 'level']]
        return df.to_dict(orient="records")
