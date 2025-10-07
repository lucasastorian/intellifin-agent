"""Safe Python expression evaluator for calculations"""
import math
import traceback
from pydantic import BaseModel, Field, ValidationError

from agent.actions.base_action import BaseAction
from agent.message import Action, Message


class PythonExec(BaseModel):
    """Execute Python code for mathematical calculations (stateless, single-execution)

    Use this tool to perform one-off calculations using Python syntax:
    - Basic math: `100 * 1.08`, `1000 / 12`, `2 ** 10`
    - Variables: `revenue = 1000; margin = 0.25; revenue * margin`
    - Functions: `round(3.14159, 2)`, `abs(-5)`, `max(10, 20, 30)`
    - Math library: `math.sqrt(16)`, `math.log(100)`, `math.exp(2)`
    - Multi-step: Use semicolons or newlines. Last expression is returned as result.

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
            return Message(role="tool", status="completed", content=str(e), error=True, action_id=action.id)

        self.log_start("PythonExec", thought=args.thought)

        try:
            # Replace semicolons with newlines for multi-statement support
            code = args.code.replace(';', '\n')

            # Create restricted namespace
            namespace = {'__builtins__': self.SAFE_BUILTINS}

            # Try to execute as expression first (single-line calculations)
            try:
                result = eval(compile(code, '<string>', 'eval'), namespace)
            except SyntaxError:
                # Code contains statements (assignments, multiple lines, etc.)
                # Split into lines and try to capture last expression
                lines = [line.strip() for line in code.strip().split('\n') if line.strip()]

                if len(lines) == 0:
                    result = None
                elif len(lines) == 1:
                    # Single statement - just exec it
                    exec(compile(code, '<string>', 'exec'), namespace)
                    result = None
                else:
                    # Execute all but last line
                    statements = '\n'.join(lines[:-1])
                    exec(compile(statements, '<string>', 'exec'), namespace)

                    # Try to evaluate last line as expression
                    last_line = lines[-1]
                    try:
                        result = eval(compile(last_line, '<string>', 'eval'), namespace)
                    except:
                        # Last line is also a statement
                        exec(compile(last_line, '<string>', 'exec'), namespace)
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
            return Message(role="tool", status="completed", content=content, action_id=action.id)

        except Exception as e:
            tb = traceback.format_exc()
            error_msg = f"Error executing code: {str(e)}\n\n```python\n{args.code}\n```\n\n```\n{tb}\n```"
            self.log_error(f"Execution failed: {e}")
            return Message(role="tool", status="completed", content=error_msg, error=True, action_id=action.id)

    @staticmethod
    def validate(action: Action) -> PythonExec:
        """Validates the action against the Pydantic schema"""
        try:
            return PythonExec(**action.body)
        except ValidationError as e:
            raise RuntimeError(f"Validation failed: {e}") from e
