"""Safe Python expression evaluator for calculations"""
import ast
import math
import traceback
from pydantic import BaseModel, Field, ValidationError

from agent.actions.base_action import BaseAction
from agent.message import Action, Message
from agent.action_response import ActionResponse


class PythonExec(BaseModel):
    """Execute Python code for mathematical calculations (stateless, single-execution)

    Use this tool to perform calculations using full Python syntax:
    - Basic math: `100 * 1.08`, `1000 / 12`, `2 ** 10`
    - Variables: `revenue = 1000\nmargin = 0.25\nrevenue * margin`
    - Functions: `round(3.14159, 2)`, `abs(-5)`, `max(10, 20, 30)`
    - Math library: `math.sqrt(16)`, `math.log(100)`, `math.exp(2)`
    - Comments: `# This is a comment\nresult = 100 * 1.08\nresult`
    - Loops/conditions: Full Python control flow is supported
    - Multi-step: Use newlines. Last expression is returned as result.

    Examples:
    ```python
    # Calculate member-months by region
    ucan = {'Q1': {'rev': 4224, 'arm': 17.30}, 'Q2': {'rev': 4296, 'arm': 17.17}}
    total_mm = 0
    for q, data in ucan.items():
        total_mm += data['rev'] / data['arm']
    total_mm  # This expression will be returned
    ```

    IMPORTANT Limitations:
    - No session state: Each call is isolated. Variables from previous PythonExec calls are NOT available.
    - No imports: Only builtins (int, float, list, dict, etc.) and `math` module are available.
    - No file I/O: Cannot read/write files, make network requests, or access external resources.
    - Calculations only: Designed for financial math, not data analysis or complex workflows.

    For complex multi-step workflows, break calculations into smaller independent calls.
    """

    thought: str = Field(
        description="Explain what calculation you're performing and what values you're using"
    )

    code: str = Field(
        description="Self-contained Python code. Must include all variable definitions needed. "
                    "Last expression is returned as the result."
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
        # Math module
        'math': math,
    }

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

        # Show code in logs (truncate if very long)
        code_preview = args.code if len(args.code) <= 100 else args.code[:97] + "..."
        self.log_start("PythonExec", f"Code: {code_preview}", thought=args.thought)

        try:
            # Create restricted namespace
            namespace = {'__builtins__': self.SAFE_BUILTINS}

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
                        exec(compile(statements, '<string>', 'exec'), namespace)

                    # Evaluate the last expression
                    expr = ast.Expression(body=last_node.value)
                    result = eval(compile(expr, '<string>', 'eval'), namespace)
                else:
                    # Last statement is not an expression (e.g., assignment, loop, etc.)
                    # Just execute everything
                    exec(compile(parsed, '<string>', 'exec'), namespace)
                    result = None
            else:
                # Empty code
                result = None

            # Format result
            if isinstance(result, float):
                # Round floats to avoid floating point noise
                if abs(result) > 1e-10:
                    result = round(result, 10)

            if result is not None:
                content = f"**Code**:\n```python\n{args.code}\n```\n\n**Result**: `{result}`"
            else:
                content = f"**Code**:\n```python\n{args.code}\n```\n\n**Executed** (no return value)"

            self.log_done(f"Result: {result}")
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
