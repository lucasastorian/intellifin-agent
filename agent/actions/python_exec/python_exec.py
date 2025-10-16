"""Safe Python expression evaluator for calculations"""
import ast
import asyncio
import io
import math
import collections
import sys
import traceback
from pydantic import BaseModel, Field, ValidationError

from agent.actions.base_action import BaseAction
from agent.message import Action, Message
from agent.action_response import ActionResponse
from agent.actions.python_exec.utils import (preprocess_code, format_syntax_error, diff_namespace, round_recursive,
                                             ASTGuard)


class PythonExec(BaseModel):
    """Execute Python code for calculations. State persists across calls - variables remain available.

    **IMPORTANT: Always return a value by:**
    - Ending with an expression: `revenue * margin`
    - OR defining `result`, `out`, `data`, or `summary`: `result = {"ev": ev, "equity": equity}`

    **Use billions for large numbers unless specified.**

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
    MAX_STDOUT = 6000
    TIMEOUT = 2.0

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
        # Limited safe standard library modules
        'collections': collections,
    }

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.namespace = {'__builtins__': self.SAFE_BUILTINS}
        # Provide a safe __import__ that only allows modules present in SAFE_BUILTINS
        self.namespace['__builtins__']['__import__'] = self._make_safe_import(self.namespace['__builtins__'])
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

        if args.reset:
            async with self._lock:
                self.namespace = {'__builtins__': self.SAFE_BUILTINS}
                # Reinstall safe __import__ after reset
                self.namespace['__builtins__']['__import__'] = self._make_safe_import(self.namespace['__builtins__'])

        var_count = len([k for k in self.namespace.keys() if k != '__builtins__'])

        code_preview = args.code if len(args.code) <= 100 else args.code[:97] + "..."
        self.log_start("PythonExec", f"Code: {code_preview} | Vars: {var_count}")

        async with self._lock:
            try:
                result_content = await asyncio.wait_for(
                    self._execute_code(args.code),
                    timeout=self.TIMEOUT
                )

                self.log_done(
                    f"Success | Vars: {len([k for k in self.namespace.keys() if k != '__builtins__'])}",
                    content=str(result_content)
                )
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
        code = preprocess_code(code)

        stdout_capture = io.StringIO()
        stderr_capture = io.StringIO()
        old_stdout = sys.stdout
        old_stderr = sys.stderr

        try:
            sys.stdout = stdout_capture
            sys.stderr = stderr_capture

            try:
                parsed = ast.parse(code, mode='exec')
            except SyntaxError as e:
                try:
                    code_retry = preprocess_code(code)
                    parsed = ast.parse(code_retry, mode='exec')
                    code = code_retry
                except SyntaxError as e2:
                    raise SyntaxError(format_syntax_error(code, e2)) from e2

            guard = ASTGuard(safe_builtins=self.SAFE_BUILTINS)
            guard.visit(parsed)

            before_ns = dict(self.namespace)

            result = None
            if parsed.body:
                last_node = parsed.body[-1]

                if isinstance(last_node, ast.Expr):
                    if len(parsed.body) > 1:
                        statements = ast.Module(body=parsed.body[:-1], type_ignores=[])
                        exec(compile(statements, '<string>', 'exec'), self.namespace)

                    expr = ast.Expression(body=last_node.value)
                    result = eval(compile(expr, '<string>', 'eval'), self.namespace)
                else:
                    exec(compile(parsed, '<string>', 'exec'), self.namespace)

                    for candidate in ("result", "out", "data", "summary"):
                        if candidate in self.namespace and candidate not in before_ns:
                            result = self.namespace[candidate]
                            break

                    if result is None:
                        diff = diff_namespace(before_ns, self.namespace)
                        if diff["added"] or diff["modified"]:
                            result = diff
            else:
                result = None

        finally:
            sys.stdout = old_stdout
            sys.stderr = old_stderr

        stdout_output = stdout_capture.getvalue()
        stderr_output = stderr_capture.getvalue()

        if len(stdout_output) > self.MAX_STDOUT:
            stdout_output = stdout_output[:self.MAX_STDOUT] + "\n...[truncated]..."

        result = round_recursive(result)

        new_var_count = len([k for k in self.namespace.keys() if k != '__builtins__'])

        content_parts = [f"```python\n{code}\n```"]

        if stdout_output.strip():
            content_parts.append(f"\n**Output**:\n```\n{stdout_output.rstrip()}\n```")

        if stderr_output.strip():
            content_parts.append(f"\n**Warnings**:\n```\n{stderr_output.rstrip()}\n```")

        if result is not None:
            if isinstance(result, dict) and "added" in result and "modified" in result:
                content_parts.append("\n**State Changes**:")
                if result["added"]:
                    content_parts.append(f"\n- Added: `{result['added']}`")
                if result["modified"]:
                    content_parts.append(f"\n- Modified: `{result['modified']}`")
            else:
                content_parts.append(f"\n**Result**: `{result}`")
        else:
            content_parts.append("\n**Executed** (no return value - end with expression or define `result`)")

        content = "".join(content_parts)

        if new_var_count > 0:
            content += f"\n\n_Session has {new_var_count} variable(s)_"

        return content

    @staticmethod
    def _make_safe_import(allowed_builtins):
        """Return a restricted __import__ that only allows whitelisted modules."""
        def _safe_import(name, globals=None, locals=None, fromlist=(), level=0):
            base = (name or "").split(".")[0]
            if base in allowed_builtins:
                # Return the top-level allowed module; attribute access resolves members
                return allowed_builtins[base]
            raise ImportError(f"Import of '{name}' is disabled for security")

        return _safe_import

    @staticmethod
    def validate(action: Action) -> PythonExec:
        """Validates the action against the Pydantic schema"""
        try:
            return PythonExec(**action.body)
        except ValidationError as e:
            raise RuntimeError(f"Validation failed: {e}") from e
