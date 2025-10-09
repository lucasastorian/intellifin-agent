"""
Stateful Python execution for financial analysis via uv + ipykernel.
- One instance = one kernel session (preserves state across calls)
- Pre-installs financial libraries: numpy, pandas, statsmodels, scipy, yfinance
- Returns Markdown-formatted outputs for DataFrames, Series, and arrays
"""

import time
import queue
import shutil
import traceback
import subprocess
from pathlib import Path
from typing import Optional, List

from pydantic import BaseModel, Field, ValidationError
from jupyter_client import KernelManager

from agent.actions.base_action import BaseAction
from agent.message import Action, Message


class PythonExec(BaseModel):
    """Execute Python code in a stateful kernel session.

    Perfect for multi-step financial analysis:
    - Load data, clean it, analyze it, model it - state persists across calls
    - Built-in libraries: numpy, pandas, statsmodels, scipy
    - DataFrames and arrays automatically render as nice Markdown tables
    - Previous variables and imports remain available
    ```
    """
    thought: Optional[str] = Field(
        default=None,
        description="Brief explanation of what this code does (optional)"
    )
    code: str = Field(
        description="Python code to execute. State persists across calls."
    )


class PythonExecAction(BaseAction):
    """Stateful Python executor using uv-managed environment"""

    name: str = "PythonExec"
    schema = PythonExec

    TIMEOUT_S: float = 10.0
    MAX_OUTPUT_CHARS: int = 15_000  # ~5k tokens

    DEFAULT_PACKAGES = [
        "numpy",
        "pandas",
        "statsmodels",
        "scipy",
        "yfinance",
        "matplotlib",
        "openpyxl",
        "numpy-financial",
    ]

    STARTUP_CODE = """
import numpy as np
import pandas as pd
import scipy
import statsmodels.api as sm
try:
    import yfinance as yf
except ImportError:
    yf = None
try:
    import numpy_financial as npf
except ImportError:
    npf = None

# Financial calculation shortcuts
if npf:
    pv = npf.pv
    fv = npf.fv
    npv = npf.npv
    irr = npf.irr
    pmt = npf.pmt

# Pandas display settings
pd.set_option("display.max_rows", 60)
pd.set_option("display.max_columns", 20)
pd.set_option("display.width", 120)
pd.set_option("display.float_format", lambda x: f"{x:,.4g}")

# Register Markdown formatters for pretty output
try:
    from IPython import get_ipython
    ip = get_ipython()
    if ip:
        md_fmt = ip.display_formatter.formatters["text/markdown"]

        # DataFrame -> Markdown table (hide index for cleaner look)
        def _df_md(df):
            try:
                if len(df) > 30:
                    return df.head(30).to_markdown(index=False) + "\\n\\n_(showing first 30 rows)_"
                return df.to_markdown(index=False)
            except:
                return str(df)

        # Series -> Markdown table
        def _series_md(s):
            try:
                return s.to_frame().to_markdown()
            except:
                return str(s)

        # NumPy array -> code block
        def _array_md(a):
            try:
                return "```\\n" + np.array2string(a, threshold=500, edgeitems=5) + "\\n```"
            except:
                return "```\\n" + str(a) + "\\n```"

        md_fmt.for_type(pd.DataFrame, _df_md)
        md_fmt.for_type(pd.Series, _series_md)
        md_fmt.for_type(np.ndarray, _array_md)
except:
    pass  # Formatters are optional
"""

    def __init__(self, session_dir: Optional[str] = None, **kwargs):
        super().__init__(**kwargs)

        if session_dir:
            self.session_dir = Path(session_dir).resolve()
        else:
            session_id = f"pykernel-{int(time.time() * 1000)}"
            self.session_dir = Path.home() / ".local" / "share" / session_id

        self.session_dir.mkdir(parents=True, exist_ok=True)

        self._init_uv_project()

        self.km = KernelManager(kernel_name="python3")
        self.km.kernel_cmd = [
            "uv", "run", "--directory", str(self.session_dir),
            "python", "-m", "ipykernel_launcher", "-f", "{connection_file}"
        ]
        self.km.start_kernel()

        self.kc = self.km.client()
        self.kc.start_channels()

        self._execute(self.STARTUP_CODE, timeout=5.0)

    async def call(self, action: Action) -> Message:
        """Execute Python code in the kernel"""
        try:
            args = self.validate(action)
        except RuntimeError as e:
            self.log_start(self.name)
            self.log_error(f"Validation failed: {e}")
            return Message(
                role="tool",
                status="completed",
                content=str(e),
                error=True,
                action_id=action.id
            )

        code_preview = args.code[:100] + "..." if len(args.code) > 100 else args.code
        self.log_start(self.name, f"Code: {code_preview}", thought=args.thought or "")

        try:
            result = self._execute(args.code, timeout=self.TIMEOUT_S)

            if result["ok"]:
                content = self._format_success(args.code, result)
                self.log_done(f"Exec #{result['count']} in {result['elapsed_ms']}ms")
                return Message(
                    role="tool",
                    status="completed",
                    content=content,
                    action_id=action.id
                )
            else:
                content = self._format_error(args.code, result)
                self.log_error("Execution error")
                return Message(
                    role="tool",
                    status="completed",
                    content=content,
                    error=True,
                    action_id=action.id
                )

        except Exception as e:
            tb = traceback.format_exc()
            self.log_error(f"Kernel failure: {e}")
            return Message(
                role="tool",
                status="completed",
                content=f"**Kernel Error**:\n```\n{e}\n```\n\n```python\n{args.code}\n```\n\n```\n{tb}\n```",
                error=True,
                action_id=action.id
            )

    @staticmethod
    def validate(action: Action) -> PythonKernelExec:
        """Validate action against schema"""
        try:
            return PythonKernelExec(**action.body)
        except ValidationError as e:
            raise RuntimeError(f"Validation failed: {e}") from e

    def _execute(self, code: str, timeout: float) -> dict:
        """Execute code and collect results"""
        msg_id = self.kc.execute(code, allow_stdin=False)
        start_time = time.time()

        stdout_lines = []
        stderr_lines = []
        errors = []
        exec_count = None
        result_markdown = None
        result_text = None

        while True:
            try:
                msg = self.kc.get_iopub_msg(timeout=timeout)
            except queue.Empty:
                self.km.interrupt_kernel()
                errors.append("⏱️ Execution timeout")
                break

            msg_type = msg["header"]["msg_type"]
            content = msg["content"]

            if msg_type == "status" and content.get("execution_state") == "idle":
                break

            if msg_type == "stream":
                text = content.get("text", "")
                if content.get("name") == "stdout":
                    stdout_lines.append(text)
                else:
                    stderr_lines.append(text)

            elif msg_type in ("execute_result", "display_data"):
                exec_count = content.get("execution_count", exec_count)
                data = content.get("data", {})

                if "text/markdown" in data and not result_markdown:
                    result_markdown = data["text/markdown"]
                if "text/plain" in data and not result_text:
                    result_text = data["text/plain"]

            elif msg_type == "error":
                exec_count = content.get("execution_count", exec_count)
                traceback_lines = content.get("traceback", [])
                clean_tb = "\n".join(self._strip_ansi(line) for line in traceback_lines)
                errors.append(clean_tb)

        elapsed_ms = round((time.time() - start_time) * 1000, 1)

        return {
            "ok": len(errors) == 0,
            "count": exec_count,
            "stdout": "".join(stdout_lines),
            "stderr": "".join(stderr_lines),
            "markdown": result_markdown,
            "text": result_text,
            "errors": errors,
            "elapsed_ms": elapsed_ms,
        }

    def _format_success(self, code: str, result: dict) -> str:
        """Format successful execution as Markdown"""
        parts = [f"```python\n{code}\n```"]

        if result["markdown"]:
            parts.append(f"\n{result['markdown']}")
        elif result["text"]:
            text = result["text"].strip("'\"")
            parts.append(f"\n**Result**: `{text}`")

        if result["stdout"].strip():
            stdout = self._truncate(result["stdout"])
            parts.append(f"\n**Output**:\n```\n{stdout}\n```")

        if result["stderr"].strip():
            stderr = self._truncate(result["stderr"])
            parts.append(f"\n**Warnings**:\n```\n{stderr}\n```")

        parts.append(f"\n_Execution #{result['count']} · {result['elapsed_ms']}ms_")

        return self._truncate("\n".join(parts))

    def _format_error(self, code: str, result: dict) -> str:
        """Format failed execution as Markdown"""
        parts = [f"```python\n{code}\n```"]

        if result["errors"]:
            error_text = "\n\n".join(result["errors"])
            parts.append(f"\n**Error**:\n```\n{error_text}\n```")

        if result["stdout"].strip():
            stdout = self._truncate(result["stdout"])
            parts.append(f"\n**Output**:\n```\n{stdout}\n```")

        if result["stderr"].strip():
            stderr = self._truncate(result["stderr"])
            parts.append(f"\n**Stderr**:\n```\n{stderr}\n```")

        return self._truncate("\n".join(parts))

    def _truncate(self, text: str) -> str:
        """Truncate to max output length"""
        if len(text) <= self.MAX_OUTPUT_CHARS:
            return text
        return text[:self.MAX_OUTPUT_CHARS] + "\n\n... _(truncated)_"

    @staticmethod
    def _strip_ansi(text: str) -> str:
        """Remove ANSI color codes from text"""
        import re
        return re.sub(r'\x1b\[[0-9;]*m', '', text)

    def _init_uv_project(self):
        """Initialize uv project and install dependencies"""
        pyproject = self.session_dir / "pyproject.toml"

        if not pyproject.exists():
            pyproject.write_text(
                '[project]\n'
                'name = "financial-analysis"\n'
                'version = "0.1.0"\n'
                'requires-python = ">=3.10"\n',
                encoding="utf-8"
            )

        subprocess.run(
            ["uv", "add"] + self.DEFAULT_PACKAGES,
            cwd=self.session_dir,
            check=True,
            capture_output=True,
            text=True
        )

    def restart(self):
        """Restart kernel (clears all state)"""
        self.km.restart_kernel(now=True)
        self._execute(self.STARTUP_CODE, timeout=5.0)
        self.log_done("Kernel restarted")

    def shutdown(self):
        """Shutdown kernel and cleanup"""
        try:
            self.kc.stop_channels()
        except:
            pass

        try:
            self.km.shutdown_kernel(now=True)
        except:
            pass

        # Clean up session directory
        try:
            shutil.rmtree(self.session_dir, ignore_errors=True)
        except:
            pass

    def __del__(self):
        """Cleanup on deletion"""
        try:
            self.shutdown()
        except:
            pass
