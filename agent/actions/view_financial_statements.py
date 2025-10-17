from typing import Literal, Optional
from datetime import date, timedelta
from pydantic import BaseModel, Field, ValidationError, field_validator
import pandas as pd
import traceback

from agent.actions.base_action import BaseAction
from agent.action_response import ActionResponse
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
            return ActionResponse(
                message=Message(role="tool", status="completed", content=str(e), error=True, action_id=action.id)
            )

        params = f"{args.symbol} {args.statement_type} ({args.report_type}), {args.start_date} → {args.end_date}"
        self.log_start("ViewFinancialStatements", params)

        # Check if company is foreign when requesting quarterly
        if args.report_type == 'quarterly':
            company_result = await (
                self.database
                .table("companies")
                .select("country")
                .contains("symbols", args.symbol)
                .execute()
            )

            if company_result.data and company_result.data[0].get('country') != 'United States':
                self.log_error(f"Foreign company - no quarterly XBRL financials")
                return ActionResponse(
                    message=Message(
                        role="tool",
                        status="completed",
                        content=(
                            f"{args.symbol} is a foreign company listed in the US. "
                            f"Foreign companies do not file quarterly XBRL financials (10-Q). "
                            f"They file annual 20-F reports and 6-K reports for material events. "
                            f"Try searching 6-K filings for interim financial updates instead."
                        ),
                        error=True,
                        action_id=action.id
                    )
                )

        # Determine forms and date range based on report type
        if args.report_type == 'quarterly':
            # Load extra year back for prior period comparisons
            load_start_date = (date.fromisoformat(args.start_date) - timedelta(days=365)).isoformat()
            forms = ['10-Q', '10-Q/A']
            fiscal_period_filter = ("neq", "FY")  # Q1, Q2, Q3, Q4
        else:
            load_start_date = args.start_date
            forms = ['10-K', '10-K/A', '20-F', '20-F/A']
            fiscal_period_filter = ("eq", "FY")  # Annual only

        not_found = await self.sync_symbols(
            symbols=[args.symbol],
            forms=forms,
            start_date=load_start_date,
            end_date=args.end_date
        )

        if not_found:
            self.log_error(f"Symbol not found: {args.symbol}")
            return ActionResponse(
                message=Message(
                    role="tool",
                    status="completed",
                    content=f"Could not find symbol {args.symbol} on EDGAR",
                    error=True,
                    action_id=action.id
                )
            )

        try:
            # Build query with appropriate fiscal_period filter
            query = (
                self.database
                .table("company_financial_statements")
                .select("data,report_date,fiscal_year,fiscal_period,form")
                .contains("company_symbols", args.symbol)
                .eq("type", args.statement_type)
            )

            # Apply fiscal_period filter
            if fiscal_period_filter[0] == "eq":
                query = query.eq("fiscal_period", fiscal_period_filter[1])
            else:
                query = query.neq("fiscal_period", fiscal_period_filter[1])

            result = await (
                query
                .gte("report_date", load_start_date)
                .lte("report_date", args.end_date)
                .execute()
            )

            if not result.data:
                self.log_done("No financial statements found", content="")
                return ActionResponse(
                    message=Message(
                        role="tool",
                        status="completed",
                        content=f"No {args.statement_type.replace('_', ' ')} data found for {args.symbol} in the specified date range.",
                        action_id=action.id
                    )
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
                self.log_done("No data after filtering", content="")
                return ActionResponse(
                    message=Message(
                        role="tool",
                        status="completed",
                        content="No data available after filtering.",
                        action_id=action.id
                    )
                )

            content = self._format_as_markdown(merged_df, args.symbol, args.statement_type, args.report_type)

            period_cols = [col for col in merged_df.columns if col not in ['concept', 'label', 'level', 'axis', 'dimension']]
            self.log_done(f"Merged {len(period_cols)} period(s)", content=content)
            return ActionResponse(
                message=Message(role="tool", status="completed", content=content, action_id=action.id)
            )

        except Exception as e:
            tb = traceback.format_exc()
            self.log_error(f"Failed to load statements: {e}\n{tb}")
            return ActionResponse(
                message=Message(
                    role="tool",
                    status="completed",
                    content=f"Error loading financial statements: {str(e)}\n\n```\n{tb}\n```",
                    error=True,
                    action_id=action.id
                )
            )

    def _format_as_markdown(self, df: pd.DataFrame, symbol: str, statement_type: str, report_type: str) -> str:
        """Format merged DataFrame as markdown table with formatted values"""
        title = f"# {symbol} - {statement_type.replace('_', ' ').title()} ({report_type.capitalize()})\n\n"

        # Normalize columns with defaults
        if 'axis' not in df.columns:
            df['axis'] = ''
        if 'dimension' not in df.columns:
            df['dimension'] = False
        if 'level' not in df.columns:
            df['level'] = 0
        if 'label' not in df.columns:
            df['label'] = df['concept']  # Fallback to concept if label missing

        # Ensure proper types - use astype with copy=False to avoid FutureWarning
        df['axis'] = df['axis'].fillna('').astype(str)
        df['label'] = df['label'].fillna('').astype(str)
        df['level'] = df['level'].fillna(0).astype(int)

        # Handle dimension separately to avoid downcasting warning
        df['dimension'] = df['dimension'].apply(lambda x: bool(x) if pd.notna(x) else False)

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
        current_axis = None

        for idx, row in display_df.iterrows():
            # Check if we're starting a new parent concept (dimension=False, level=0)
            if row['dimension'] == False and row['level'] == 0:
                current_axis = None
                # Add parent row with indentation
                label = '  ' * int(row['level']) + row['label']
                output_rows.append([label] + [row[col] for col in period_cols])

            # Check if we're in a dimensional fact (dimension=True)
            elif row['dimension'] == True:
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
    def _get_axis_label(axis) -> str:
        """Map axis identifier to friendly label"""
        # Handle non-string axis values (floats, NaN, etc.)
        if not isinstance(axis, str):
            return str(axis) if axis and str(axis) != 'nan' else ''

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
                return f"${num / 1_000_000_000:.3f}B"
            elif abs(num) >= 1_000_000:
                return f"${num / 1_000_000:.3f}M"
            elif abs(num) >= 1_000:
                return f"${num / 1_000:.3f}K"
            else:
                return f"${num:.3f}"
        except (ValueError, TypeError):
            return str(val)

    @staticmethod
    def validate(action: Action) -> ViewFinancialStatements:
        """Validates the action against the Pydantic schema"""
        try:
            return ViewFinancialStatements(**action.body)
        except ValidationError as e:
            raise RuntimeError(f"Validation failed: {e}") from e
