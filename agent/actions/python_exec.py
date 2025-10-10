"""Safe Python expression evaluator for calculations"""
import ast
import io
import math
import sys
import traceback
from pydantic import BaseModel, Field, ValidationError

from agent.actions.base_action import BaseAction
from agent.message import Action, Message
from agent.action_response import ActionResponse


class PythonExec(BaseModel):
    """Execute Python code for calculations. State persists across calls - variables remain available.

    Supports: math operations, control flow (loops/conditions), comments, print statements, builtins (round, sum, max, etc.), math module

    IMPORTANT: End with an expression (not assignment) to return a value.
    - Returns value: `total` or `revenue * margin`
    - No return: `x = 100` or `total = sum(values)`
    - Can also use print() for intermediate outputs

    Set reset=True to clear all variables and start fresh.
    """

    thought: str = Field(
        description="Brief explanation of what this calculation does and what you're computing"
    )

    code: str = Field(
        description="Python code to execute. IMPORTANT: Variables from previous PythonExec calls "
                    "persist and are available. End with an expression (not assignment) to return a value."
    )

    reset: bool = Field(
        default=False,
        description="Set to True to clear all session variables and start fresh. "
                    "Use when beginning a completely new, unrelated calculation."
    )


class PythonExecAction(BaseAction):
    """Safe Python executor for calculations"""

    name: str = 'PythonExec'
    schema = PythonExec

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
    }

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        # Persistent namespace - survives across calls in the same session
        self.namespace = {'__builtins__': self.SAFE_BUILTINS}

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
            self.namespace = {'__builtins__': self.SAFE_BUILTINS}

        # Count user-defined variables (exclude builtins)
        var_count = len([k for k in self.namespace.keys() if k != '__builtins__'])

        # Show code in logs (truncate if very long)
        code_preview = args.code if len(args.code) <= 100 else args.code[:97] + "..."
        self.log_start("PythonExec", f"Code: {code_preview} | Vars: {var_count}", thought=args.thought)

        try:
            # Capture stdout and stderr
            stdout_capture = io.StringIO()
            stderr_capture = io.StringIO()
            old_stdout = sys.stdout
            old_stderr = sys.stderr

            try:
                # Redirect output
                sys.stdout = stdout_capture
                sys.stderr = stderr_capture

                # Parse code into AST to properly handle multi-line code and comments
                try:
                    parsed = ast.parse(args.code, mode='exec')
                except SyntaxError as e:
                    raise SyntaxError(f"Invalid Python syntax: {e}")

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
                        # Last statement is not an expression (e.g., assignment, loop, etc.)
                        # Just execute everything
                        exec(compile(parsed, '<string>', 'exec'), self.namespace)
                        result = None
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

            # Format result
            if isinstance(result, float):
                # Round floats to avoid floating point noise
                if abs(result) > 1e-10:
                    result = round(result, 10)

            # Count variables after execution
            new_var_count = len([k for k in self.namespace.keys() if k != '__builtins__'])

            # Build response content
            content_parts = [f"```python\n{args.code}\n```"]

            # Add stdout if present
            if stdout_output.strip():
                content_parts.append(f"\n**Output**:\n```\n{stdout_output.rstrip()}\n```")

            # Add stderr if present
            if stderr_output.strip():
                content_parts.append(f"\n**Warnings**:\n```\n{stderr_output.rstrip()}\n```")

            # Add result
            if result is not None:
                content_parts.append(f"\n**Result**: `{result}`")
            elif not stdout_output.strip():
                # Only show "no return value" if there's also no stdout
                content_parts.append("\n**Executed** (no return value)")

            content = "".join(content_parts)

            # Add state info if variables exist
            if new_var_count > 0:
                content += f"\n\n_Session has {new_var_count} variable(s)_"

            self.log_done(f"Result: {result} | Vars: {new_var_count}")
            return ActionResponse(
                message=Message(role="tool", status="completed", content=content, action_id=action.id)
            )

        except Exception as e:
            tb = traceback.format_exc()
            error_msg = f"Error executing code: {str(e)}\n\n```python\n{args.code}\n```\n\n```\n{tb}\n```"
            self.log_error(f"Execution failed: {e}")
            return ActionResponse(
                message=Message(role="tool", status="completed", content=error_msg, error=True, action_id=action.id)
            )

    @staticmethod
    def validate(action: Action) -> PythonExec:
        """Validates the action against the Pydantic schema"""
        try:
            return PythonExec(**action.body)
        except ValidationError as e:
            raise RuntimeError(f"Validation failed: {e}") from e
