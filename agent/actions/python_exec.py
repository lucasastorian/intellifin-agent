from typing import Literal, Optional
from pydantic import BaseModel, Field


class PythonExec(BaseModel):
    """Execute a Python code block inside the persistent, sandboxed IPython session (timeout of 5s).

    Behavior:
    - Preserves session state between calls (variables, imports).
    - Returns structured outputs (stdout/stderr + last value/display).
    - Enforces a 5-s wall-clock timeout in the worker.
    """
    code: str = Field(..., description="Python code to execute. Treats each call as a new 'cell'.")
