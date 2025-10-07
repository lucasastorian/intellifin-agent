from typing import Literal, Optional
from datetime import date, timedelta
from pydantic import BaseModel, Field, ValidationError, field_validator
import pandas as pd

from agent.actions.base_action import BaseAction
from agent.message import Action, Message
from utils.financial_statement_merger import FinancialStatementMerger


class ViewFinancialStatements(BaseModel):
    """View core financial statements (income statement, balance sheet, or cash flow) for a company across multiple periods

    This action loads financial statements for a company across a date range and merges them into a single
    comparative table showing values side-by-side for each reporting period.

    - Filters out segment/dimensional data (dimension=False only) unless include_segments=True
    """
    symbol: str = Field(description="The ticker symbol of the company")
    statement_type: Literal['income_statement', 'balance_sheet', 'cash_flow'] = Field(
        description="Type of financial statement to view")
    report_type: Literal['annual', 'quarterly'] = Field(
        description="Whether to load annual (10-K) or quarterly (10-Q) statements")
    start_date: str = Field(description="Start date (YYYY-MM-DD) for report_date range")
    end_date: Optional[str] = Field(default=None, description="End date (YYYY-MM-DD) for report_date range. Defaults to today.")
    include_segments: bool = Field(default=False, description="Whether to include segment breakdowns (dimension=True facts)")

    @field_validator("symbol")
    @classmethod
    def norm_symbol(cls, v: str) -> str:
        return v.strip().upper()

    @field_validator("start_date")
    @classmethod
    def check_start_date(cls, v: str) -> str:
        _ = date.fromisoformat(v)
        return v

    @field_validator("end_date")
    @classmethod
    def check_end_date(cls, v: Optional[str]) -> str:
        if v is None:
            return date.today().isoformat()
        _ = date.fromisoformat(v)
        return v


class ViewFinancialStatementsAction(BaseAction):
    name: str = 'ViewFinancialStatements'
    schema = ViewFinancialStatements

    async def call(self, action: Action):
        """Load and merge financial statements across reporting periods"""
        try:
            args = self.validate(action)
        except RuntimeError as e:
            self.log_start("ViewFinancialStatements")
            self.log_error(f"Validation failed: {e}")
            return Message(role="tool", status="completed", content=str(e), error=True, action_id=action.id)

        params = f"{args.symbol} {args.statement_type} ({args.report_type}), {args.start_date} → {args.end_date}"
        self.log_start("ViewFinancialStatements", params)

        not_found = self.sync_symbols(symbols=[args.symbol])
        if not_found:
            self.log_error(f"Symbol not found: {args.symbol}")
            return Message(
                role="tool",
                status="completed",
                content=f"Could not find symbol {args.symbol} on EDGAR",
                error=True,
                action_id=action.id
            )

        if args.report_type == 'quarterly':
            load_start_date = (date.fromisoformat(args.start_date) - timedelta(days=365)).isoformat()
            forms = ['10-Q', '10-Q/A', '10-K', '10-K/A', '20-F', '20-F/A']
        else:
            load_start_date = args.start_date
            forms = ['10-K', '10-K/A', '20-F', '20-F/A']

        try:
            result = (
                self.database
                .table("company_financial_statements")
                .select("data,report_date,fiscal_year,fiscal_period,form")
                .contains("company_symbols", args.symbol)
                .eq("type", args.statement_type)
                .in_("form", forms)
                .gte("report_date", load_start_date)
                .lte("report_date", args.end_date)
                .execute()
            )

            if not result.data:
                self.log_done("No financial statements found")
                return Message(
                    role="tool",
                    status="completed",
                    content=f"No {args.statement_type.replace('_', ' ')} data found for {args.symbol} in the specified date range.",
                    action_id=action.id
                )

            merger = FinancialStatementMerger(
                statements=result.data,
                report_type=args.report_type,
                include_segments=args.include_segments,
                requested_start_date=args.start_date,
                requested_end_date=args.end_date
            )

            merged_df = merger.merge()

            if merged_df.empty:
                self.log_done("No data after filtering")
                return Message(
                    role="tool",
                    status="completed",
                    content="No data available after filtering.",
                    action_id=action.id
                )

            # Format as markdown table
            content = self._format_as_markdown(merged_df, args.symbol, args.statement_type, args.report_type)

            # Count periods in final output
            period_cols = [col for col in merged_df.columns if col not in ['concept', 'label', 'level', 'axis', 'dimension']]
            self.log_done(f"Merged {len(period_cols)} period(s)")
            return Message(role="tool", status="completed", content=content, action_id=action.id)

        except Exception as e:
            self.log_error(f"Failed to load statements: {e}")
            return Message(
                role="tool",
                status="completed",
                content=f"Error loading financial statements: {str(e)}",
                error=True,
                action_id=action.id
            )

    def _format_as_markdown(self, df: pd.DataFrame, symbol: str, statement_type: str, report_type: str) -> str:
        """Format merged DataFrame as markdown table with formatted values"""
        title = f"# {symbol} - {statement_type.replace('_', ' ').title()} ({report_type.capitalize()})\n\n"

        # Get period columns (exclude metadata)
        meta_cols = ['concept', 'label', 'level', 'axis', 'dimension', 'member']
        period_cols = [col for col in df.columns if col not in meta_cols]

        # Format values for display (exclude member column from output)
        display_df = df[['label', 'level', 'axis', 'dimension'] + period_cols].copy()

        # Format numeric values
        for col in period_cols:
            display_df[col] = display_df[col].apply(self._format_value)

        # Add indentation and axis grouping labels
        output_rows = []
        current_concept = None
        current_axis = None

        for idx, row in display_df.iterrows():
            # Check if we're starting a new parent concept (dimension=False, level=0)
            if not row['dimension'] and row['level'] == 0:
                current_concept = row['label']
                current_axis = None
                # Add parent row with indentation
                label = '  ' * int(row['level']) + row['label']
                output_rows.append([label] + [row[col] for col in period_cols])

            # Check if we're in a dimensional fact (dimension=True)
            elif row['dimension']:
                axis = row.get('axis', '')

                # If axis changed, insert axis group header
                if axis and axis != current_axis:
                    current_axis = axis
                    axis_label = self._get_axis_label(axis)
                    # Add axis header row (indented once, no values)
                    output_rows.append(['  ' + f"[{axis_label}]"] + ['-'] * len(period_cols))

                # Add dimensional fact row (indented based on level + 1 for axis grouping)
                indent = '  ' * (int(row['level']) + 1)
                label = indent + row['label']
                output_rows.append([label] + [row[col] for col in period_cols])

            # Other cases (abstract, etc.)
            else:
                current_axis = None
                label = '  ' * int(row['level']) + row['label']
                output_rows.append([label] + [row[col] for col in period_cols])

        # Create final DataFrame for markdown
        final_df = pd.DataFrame(output_rows, columns=['label'] + period_cols)
        markdown = final_df.to_markdown(index=False)

        return title + markdown

    @staticmethod
    def _get_axis_label(axis: str) -> str:
        """Map axis identifier to friendly label"""
        axis_map = {
            'srt:ProductOrServiceAxis': 'Product/Service Breakdown',
            'us-gaap:StatementBusinessSegmentsAxis': 'Business Segment Breakdown',
            'srt:StatementGeographicalAxis': 'Geographic Breakdown',
            'us-gaap:StatementGeographicalAxis': 'Geographic Breakdown',
            'us-gaap:StatementEquityComponentsAxis': 'Equity Components',
            'srt:ConsolidationItemsAxis': 'Consolidation Items',
        }
        return axis_map.get(axis, axis.split(':')[-1] if ':' in axis else axis)

    @staticmethod
    def _format_value(val) -> str:
        """Format numeric values in millions/billions"""
        if pd.isna(val) or val == '' or val == 0:
            return '-'

        try:
            num = float(val)
            if abs(num) >= 1_000_000_000:
                return f"${num / 1_000_000_000:.2f}B"
            elif abs(num) >= 1_000_000:
                return f"${num / 1_000_000:.2f}M"
            elif abs(num) >= 1_000:
                return f"${num / 1_000:.2f}K"
            else:
                return f"${num:.2f}"
        except (ValueError, TypeError):
            return str(val)

    @staticmethod
    def validate(action: Action) -> ViewFinancialStatements:
        """Validates the action against the Pydantic schema"""
        try:
            return ViewFinancialStatements(**action.body)
        except ValidationError as e:
            raise RuntimeError(f"Validation failed: {e}") from e
