"""Safe Python expression evaluator for calculations"""
import ast
import asyncio
import io
import json
import math
import re
import sys
import textwrap
import traceback
import unicodedata
from pydantic import BaseModel, Field, ValidationError

from agent.actions.base_action import BaseAction
from agent.message import Action, Message
from agent.action_response import ActionResponse


# Finance helper functions
def npv(rate, cashflows):
    """Calculate net present value of cashflows at given discount rate"""
    return sum(cf / ((1 + rate) ** t) for t, cf in enumerate(cashflows, start=1))


def gordon(cashflow_next, rate, growth):
    """Calculate terminal value using Gordon Growth Model"""
    if rate <= growth:
        raise ValueError("Discount rate must exceed growth rate")
    return cashflow_next / (rate - growth)


def dcf_fcff(fcff, wacc, g):
    """Calculate enterprise value using DCF of FCFF

    Args:
        fcff: list of yearly FCFF numbers (Y1..Yn)
        wacc: weighted average cost of capital
        g: terminal growth rate

    Returns:
        Enterprise value (PV of explicit period + PV of terminal value)
    """
    n = len(fcff)
    pv = sum(cf / ((1 + wacc) ** t) for t, cf in enumerate(fcff, start=1))
    tv = fcff[-1] * (1 + g) / (wacc - g)
    pv_tv = tv / ((1 + wacc) ** n)
    return pv + pv_tv


def bps(x):
    """Convert decimal to basis points"""
    return x * 10000.0


def pct(x):
    """Convert decimal to percentage"""
    return x * 100.0


def preprocess_code(src: str) -> str:
    """Normalize code to handle paste gremlins and inconsistent formatting"""
    # Strip ``` fences if present
    if src.strip().startswith("```"):
        src = re.sub(r"^```[a-zA-Z0-9_+-]*\n", "", src.strip(), count=1)
        if src.strip().endswith("```"):
            src = src.strip()[:-3]

    # Normalize unicode and line endings
    src = unicodedata.normalize("NFKC", src).replace("\r\n", "\n").replace("\r", "\n")

    # Convert tabs to spaces
    src = src.replace("\t", "    ")

    # Dedent common leading whitespace
    src = textwrap.dedent(src)

    # Strip trailing whitespace from each line
    src = "\n".join(line.rstrip() for line in src.split("\n"))

    return src


def format_syntax_error(src: str, e: SyntaxError) -> str:
    """Format syntax error with context lines and pointer"""
    lineno = (e.lineno or 1) - 1
    lines = src.split("\n")
    start = max(0, lineno - 2)
    end = min(len(lines), lineno + 3)

    caret = ""
    if e.offset and 0 <= lineno < len(lines):
        caret = " " * (e.offset - 1) + "^"

    block = "\n".join(f"{i+1:>4}: {lines[i]}" for i in range(start, end))
    if caret:
        block += f"\n      {caret}"

    return f"{block}\n\n{e.msg} at line {e.lineno}, column {e.offset}"


def diff_namespace(before: dict, after: dict) -> dict:
    """Show what changed in the namespace"""
    # Exclude builtins
    b = {k: v for k, v in before.items() if k != "__builtins__"}
    a = {k: v for k, v in after.items() if k != "__builtins__"}

    added = {k: a[k] for k in a.keys() - b.keys()}
    modified = {k: a[k] for k in a.keys() & b.keys() if a[k] is not b[k]}

    return {"added": added, "modified": modified}


def round_recursive(obj, decimals=10):
    """Recursively round floats in nested structures"""
    if isinstance(obj, float):
        if abs(obj) > 1e-10:
            return round(obj, decimals)
        return obj
    elif isinstance(obj, dict):
        return {k: round_recursive(v, decimals) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [round_recursive(item, decimals) for item in obj]
    elif isinstance(obj, tuple):
        return tuple(round_recursive(item, decimals) for item in obj)
    return obj


class ASTGuard(ast.NodeVisitor):
    """Security guard to prevent unsafe AST nodes"""

    def visit_Import(self, node):
        raise SyntaxError("Imports are disabled for security")

    def visit_ImportFrom(self, node):
        raise SyntaxError("Imports are disabled for security")

    def visit_Name(self, node):
        if "__" in node.id:
            raise SyntaxError(f"Dunder access is disabled: {node.id}")
        self.generic_visit(node)

    def visit_Attribute(self, node):
        if isinstance(node.attr, str) and "__" in node.attr:
            raise SyntaxError(f"Dunder access is disabled: {node.attr}")
        self.generic_visit(node)


class PythonExec(BaseModel):
    """Execute Python code for calculations. State persists across calls - variables remain available.

    **IMPORTANT: Always return a value by:**
    - Ending with an expression: `revenue * margin`
    - OR defining `result`, `out`, `data`, or `summary`: `result = {"ev": ev, "equity": equity}`

    **Use billions for large numbers unless specified.**

    **Prefer built-in finance helpers to reduce errors:**
    - `npv(rate, cashflows)` - Net present value
    - `gordon(cf_next, rate, growth)` - Terminal value using Gordon Growth
    - `dcf_fcff(fcff, wacc, g)` - Enterprise value from FCFF list
    - `bps(x)` - Convert to basis points (x * 10000)
    - `pct(x)` - Convert to percentage (x * 100)

    **Examples:**
    ```python
    # DCF with helpers
    fcff = [120, 135, 150, 165, 180]
    ev = dcf_fcff(fcff, 0.10, 0.03)
    result = {"ev": ev, "per_share": ev / shares}
    ```

    ```python
    # Manual NPV calculation
    cashflows = [100, 110, 121]
    pv = npv(0.08, cashflows)
    pv  # Return value
    ```

    Supports: math operations, control flow, comments, print(), builtins (round, sum, max, etc.), math module

    Set reset=True to clear all variables and start fresh.
    """

    code: str = Field(
        description="Python code to execute. Variables persist across calls. "
                    "MUST end with an expression OR define result/out/data/summary to return a value."
    )

    reset: bool = Field(
        default=False,
        description="Set to True to clear all session variables and start fresh"
    )


class PythonExecAction(BaseAction):
    """Safe Python executor for calculations"""

    name: str = 'PythonExec'
    schema = PythonExec
    MAX_STDOUT = 6000  # Limit stdout to prevent token bloat
    TIMEOUT = 2.0  # 2 second timeout for calculations

    # Safe builtins for calculations only
    SAFE_BUILTINS = {
        'abs': abs,
        'round': round,
        'min': min,
        'max': max,
        'sum': sum,
        'len': len,
        'pow': pow,
        'int': int,
        'float': float,
        'str': str,
        'bool': bool,
        'list': list,
        'dict': dict,
        'tuple': tuple,
        'range': range,
        'enumerate': enumerate,
        'zip': zip,
        'sorted': sorted,
        'reversed': reversed,
        'any': any,
        'all': all,
        'print': print,
        # Math module
        'math': math,
        # Finance helpers
        'npv': npv,
        'gordon': gordon,
        'dcf_fcff': dcf_fcff,
        'bps': bps,
        'pct': pct,
    }

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        # Persistent namespace - survives across calls in the same session
        self.namespace = {'__builtins__': self.SAFE_BUILTINS}
        # Lock to prevent concurrent modifications
        self._lock = asyncio.Lock()

    async def call(self, action: Action):
        """Execute Python code safely"""
        try:
            args = self.validate(action)
        except RuntimeError as e:
            self.log_start("PythonExec")
            self.log_error(f"Validation failed: {e}")
            return ActionResponse(
                message=Message(role="tool", status="completed", content=str(e), error=True, action_id=action.id)
            )

        # Handle reset request
        if args.reset:
            async with self._lock:
                self.namespace = {'__builtins__': self.SAFE_BUILTINS}

        # Count user-defined variables (exclude builtins)
        var_count = len([k for k in self.namespace.keys() if k != '__builtins__'])

        # Show code in logs (truncate if very long)
        code_preview = args.code if len(args.code) <= 100 else args.code[:97] + "..."
        self.log_start("PythonExec", f"Code: {code_preview} | Vars: {var_count}")

        # Acquire lock to prevent concurrent execution
        async with self._lock:
            try:
                # Execute with timeout
                result_content = await asyncio.wait_for(
                    self._execute_code(args.code),
                    timeout=self.TIMEOUT
                )

                self.log_done(f"Success | Vars: {len([k for k in self.namespace.keys() if k != '__builtins__'])}")
                return ActionResponse(
                    message=Message(role="tool", status="completed", content=result_content, action_id=action.id)
                )

            except asyncio.TimeoutError:
                error_msg = (
                    f"**Timeout**: Code exceeded {self.TIMEOUT}s limit.\n\n"
                    f"```python\n{args.code}\n```\n\n"
                    f"Consider simplifying or splitting the calculation."
                )
                self.log_error(f"Timeout after {self.TIMEOUT}s")
                return ActionResponse(
                    message=Message(role="tool", status="completed", content=error_msg, error=True, action_id=action.id)
                )

            except Exception as e:
                tb = traceback.format_exc()
                error_msg = f"**Error**: {str(e)}\n\n```python\n{args.code}\n```\n\n```\n{tb}\n```"
                self.log_error(f"Execution failed: {e}")
                return ActionResponse(
                    message=Message(role="tool", status="completed", content=error_msg, error=True, action_id=action.id)
                )

    async def _execute_code(self, code: str) -> str:
        """Execute code and return formatted result"""
        # Preprocess code
        code = preprocess_code(code)

        # Capture stdout and stderr
        stdout_capture = io.StringIO()
        stderr_capture = io.StringIO()
        old_stdout = sys.stdout
        old_stderr = sys.stderr

        try:
            # Redirect output
            sys.stdout = stdout_capture
            sys.stderr = stderr_capture

            # Parse code into AST
            try:
                parsed = ast.parse(code, mode='exec')
            except SyntaxError as e:
                # Try preprocessing again (in case it wasn't normalized enough)
                try:
                    code_retry = preprocess_code(code)
                    parsed = ast.parse(code_retry, mode='exec')
                    code = code_retry  # Use the preprocessed version
                except SyntaxError as e2:
                    # Still failed, format helpful error
                    raise SyntaxError(format_syntax_error(code, e2)) from e2

            # Security check
            guard = ASTGuard()
            guard.visit(parsed)

            # Snapshot namespace before execution
            before_ns = dict(self.namespace)

            # Check if the last statement is an expression we can capture
            result = None
            if parsed.body:
                last_node = parsed.body[-1]

                # If last statement is an expression, capture its value
                if isinstance(last_node, ast.Expr):
                    # Execute everything except the last expression
                    if len(parsed.body) > 1:
                        statements = ast.Module(body=parsed.body[:-1], type_ignores=[])
                        exec(compile(statements, '<string>', 'exec'), self.namespace)

                    # Evaluate the last expression
                    expr = ast.Expression(body=last_node.value)
                    result = eval(compile(expr, '<string>', 'eval'), self.namespace)
                else:
                    # Last statement is not an expression
                    exec(compile(parsed, '<string>', 'exec'), self.namespace)

                    # Try heuristics: check for result/out/data/summary
                    for candidate in ("result", "out", "data", "summary"):
                        if candidate in self.namespace and candidate not in before_ns:
                            result = self.namespace[candidate]
                            break

                    # If still nothing, show state diff
                    if result is None:
                        diff = diff_namespace(before_ns, self.namespace)
                        if diff["added"] or diff["modified"]:
                            result = diff
            else:
                # Empty code
                result = None

        finally:
            # Always restore original stdout/stderr
            sys.stdout = old_stdout
            sys.stderr = old_stderr

        # Get captured output
        stdout_output = stdout_capture.getvalue()
        stderr_output = stderr_capture.getvalue()

        # Bound stdout
        if len(stdout_output) > self.MAX_STDOUT:
            stdout_output = stdout_output[:self.MAX_STDOUT] + "\n...[truncated]..."

        # Round floats to avoid noise
        result = round_recursive(result)

        # Count variables after execution
        new_var_count = len([k for k in self.namespace.keys() if k != '__builtins__'])

        # Build response content
        content_parts = [f"```python\n{code}\n```"]

        # Add stdout if present
        if stdout_output.strip():
            content_parts.append(f"\n**Output**:\n```\n{stdout_output.rstrip()}\n```")

        # Add stderr if present
        if stderr_output.strip():
            content_parts.append(f"\n**Warnings**:\n```\n{stderr_output.rstrip()}\n```")

        # Add result
        if result is not None:
            # If result is a dict with added/modified (state diff), format specially
            if isinstance(result, dict) and "added" in result and "modified" in result:
                content_parts.append("\n**State Changes**:")
                if result["added"]:
                    content_parts.append(f"\n- Added: `{result['added']}`")
                if result["modified"]:
                    content_parts.append(f"\n- Modified: `{result['modified']}`")
            else:
                content_parts.append(f"\n**Result**: `{result}`")
        elif not stdout_output.strip():
            # Only show "no return value" if there's also no stdout
            content_parts.append("\n**Executed** (no return value - end with expression or define `result`)")

        content = "".join(content_parts)

        # Add state info if variables exist
        if new_var_count > 0:
            content += f"\n\n_Session has {new_var_count} variable(s)_"

        return content

    @staticmethod
    def validate(action: Action) -> PythonExec:
        """Validates the action against the Pydantic schema"""
        try:
            return PythonExec(**action.body)
        except ValidationError as e:
            raise RuntimeError(f"Validation failed: {e}") from e
