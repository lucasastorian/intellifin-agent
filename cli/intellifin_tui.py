"""Interactive Textual UI for IntelliFin Agent - Synthwave Edition."""
import sys
import re
import asyncio
from collections import deque
from dataclasses import dataclass
from typing import Callable, List, Optional
from textual.app import App, ComposeResult
from textual.widgets import Input, RichLog, Header, Footer, Label, Static, OptionList
from textual.widgets.option_list import Option
from textual.containers import Container
from textual.binding import Binding
from textual.reactive import reactive
from textual.screen import ModalScreen
from textual.events import Key
from textual import on

from agent.agent import Agent
from agent.agent_config import AgentMode
from cli.agent_runner import AgentRunner
from agent.stream_events import StreamEvent

# TRON Legacy color palette - clean terminal aesthetic
TRON_CYAN = "#00faff"
TRON_DIM = "#007a8c"
TRON_TEXT = "#9efcff"
TRON_AMBER = "#ffb000"
TRON_BG = "#000507"

# Legacy aliases for compatibility
NEON_PINK = TRON_AMBER
NEON_CYAN = TRON_CYAN
NEON_GREEN = TRON_CYAN
DEEP_PURPLE = TRON_BG
MID_PURPLE = TRON_BG
INK = TRON_TEXT


@dataclass
class Command:
    name: str
    help: str
    run: Callable[[], None]


class InlineCommandList(Container):
    """Claude-style inline command list shown below the prompt."""

    CSS = f"""
    InlineCommandList {{
        width: 100%;
        height: auto;
        border: solid {TRON_DIM};
        background: {TRON_BG};
        padding: 0 1;
        dock: bottom;
        overflow: hidden auto;
    }}
    InlineCommandList OptionList {{
        background: {TRON_BG};
        color: {TRON_TEXT};
        height: auto;
        max-height: 10;
    }}
    """

    def __init__(self, commands: List[Command], **kwargs):
        super().__init__(**kwargs)
        self._all = commands
        self._filtered = commands

    @staticmethod
    def _ellipsize(text: str, n: int = 60) -> str:
        """Truncate text to n characters with ellipsis."""
        return text if len(text) <= n else text[:n - 1] + "…"

    def compose(self) -> ComposeResult:
        yield OptionList(*(Option(f"/{c.name} — {self._ellipsize(c.help)}", id=c.name) for c in self._all))

    def filter(self, q: str) -> int:
        """Update options; return number of matches."""
        ql = q.lower().lstrip("/")
        ol = self.query_one(OptionList)
        ol.clear_options()
        if not ql:
            self._filtered = []
            return 0
        starts = [c for c in self._all if c.name.startswith(ql)]
        subs = [c for c in self._all if ql in c.name and c not in starts]
        self._filtered = starts + subs
        for c in self._filtered:
            ol.add_option(Option(f"/{c.name} — {self._ellipsize(c.help)}", id=c.name))
        if self._filtered:
            ol.highlighted = 0
        return len(self._filtered)

    def current_command(self) -> Optional[Command]:
        """Get the currently highlighted command."""
        if not self._filtered:
            return None
        ol = self.query_one(OptionList)
        idx = ol.highlighted
        if idx is None or idx < 0 or idx >= len(self._filtered):
            return None
        return self._filtered[idx]

    @on(OptionList.OptionSelected)
    def _on_select(self, ev: OptionList.OptionSelected) -> None:
        """Handle command selection."""
        match = next((c for c in self._filtered if c.name == ev.option.id), None)
        if match:
            self.styles.display = "none"
            # Clear the prompt input
            prompt = self.app.query_one("#prompt", Input)
            prompt.value = ""
            self.app.call_after_refresh(match.run)


class ModelPicker(ModalScreen[Optional[str]]):
    """Modal to select AI model."""

    CSS = f"""
    ModelPicker {{
        align: center middle;
    }}
    #mpanel {{
        width: 60%;
        max-width: 80;
        border: solid {TRON_AMBER};
        background: {TRON_BG};
    }}
    #mtitle {{
        content-align: center middle;
        padding: 1 1;
        color: {TRON_CYAN};
        text-style: bold;
    }}
    #olist {{
        height: 6;
        background: {TRON_BG};
    }}
    """

    def __init__(self, models: List[str], current: str):
        super().__init__()
        self.models = models
        self.current = current

    def compose(self) -> ComposeResult:
        with Container(id="mpanel"):
            yield Static("Select Model", id="mtitle")
            yield OptionList(
                *(Option(("✔ " if m == self.current else "  ") + m, id=m) for m in self.models),
                id="olist"
            )

    def on_mount(self) -> None:
        self.screen.set_focus(self.query_one("#olist", OptionList))

    def on_option_list_option_selected(self, ev: OptionList.OptionSelected) -> None:
        self.dismiss(ev.option.id)

    def on_key(self, ev: Key) -> None:
        if ev.key == "escape":
            ev.stop()
            self.dismiss(None)


class IntelliFinTUI(App):
    """Interactive TUI for IntelliFin financial research agent (TRON terminal aesthetic)."""

    # Regex to insert zero-width spaces in long unbroken runs
    _HARD_WRAP_RE = re.compile(r"(\S{120})")

    CSS = f"""
    Screen {{
        background: {TRON_BG};
        color: {TRON_TEXT};
    }}

    #banner {{
        dock: top;
        height: 3;
        background: {TRON_BG};
        border: solid {TRON_CYAN};
        border-title-align: left;
        padding: 0 2;
        content-align: left middle;
        text-style: bold;
        color: {TRON_CYAN};
    }}

    #output {{
        height: 1fr;
        color: {TRON_TEXT};
        background: {TRON_BG};
        border: solid {TRON_CYAN};
        overflow: hidden auto;
        padding: 1;
        word-break: break-word;
    }}

    #status-bar {{
        dock: bottom;
        height: 1;
        background: {TRON_BG};
        border-top: solid {TRON_DIM};
        padding: 0 1;
    }}

    #status-label {{
        color: {TRON_CYAN};
    }}

    .status-idle {{
        color: {TRON_CYAN};
    }}

    .status-running {{
        color: {TRON_AMBER};
        text-style: bold;
    }}

    #prompt {{
        dock: bottom;
        margin: 0 1 1 1;
        border: solid {TRON_CYAN};
    }}

    Input {{
        background: {TRON_BG};
        color: {TRON_TEXT};
        border: none;
    }}

    Input:focus {{
        border: solid {TRON_CYAN};
        text-style: bold;
    }}

    Input:disabled {{
        opacity: 0.5;
        color: {TRON_DIM};
    }}

    Header, Footer {{
        background: {TRON_BG};
        color: {TRON_DIM};
    }}
    """

    BINDINGS = [
        Binding("escape", "abort", "Abort", show=True, priority=True),
        Binding("ctrl+c", "quit", "Quit", show=True),
        Binding("ctrl+l", "clear", "Clear", show=False),
    ]

    # Use reactive for state tracking
    query_running = reactive(False)

    def __init__(self, agent: Agent):
        super().__init__()
        self.runner = AgentRunner(agent)
        self.consumer_task: asyncio.Task | None = None
        self._pulse_on = True

        # Buffering for performance
        self._buf_msgs: deque = deque()

    def watch_query_running(self, is_running: bool) -> None:
        """Watch for query_running state changes and disable/enable prompt."""
        try:
            prompt = self.query_one("#prompt", Input)
            prompt.disabled = is_running
        except Exception:
            # Ignore if widget not yet mounted
            pass

    def _hard_wrap(self, s: str) -> str:
        """Insert zero-width spaces in long unbroken runs to enable wrapping."""
        return self._HARD_WRAP_RE.sub(r"\1\u200b", s)

    def _append_msg(self, s: str) -> None:
        """Buffer message for batched flush."""
        self._buf_msgs.append(self._hard_wrap(s))

    def _flush_buffers(self) -> None:
        """Flush buffered messages to output (called every 50ms)."""
        if self._buf_msgs:
            out = self.query_one("#output", RichLog)
            out.write("".join(self._buf_msgs))
            self._buf_msgs.clear()

    def compose(self) -> ComposeResult:
        """Compose the UI layout."""
        yield Header()
        yield Static("▉ INTELLIFIN TERMINAL • FINANCIAL INTELLIGENCE SYSTEM", id="banner")
        yield RichLog(id="output", max_lines=5000, markup=False, highlight=False, wrap=True)
        with Container(id="status-bar"):
            yield Label("▉ IDLE", id="status-label", classes="status-idle")
        # Command list (inline, shown when typing '/')
        cmds = [
            Command("model", "Set the AI model", run=self._open_model_picker),
            Command("clear", "Clear output", run=lambda: asyncio.create_task(self.action_clear())),
            Command("abort", "Cancel running job", run=lambda: asyncio.create_task(self.action_abort())),
            Command("status", "Show current status & model", run=lambda: asyncio.create_task(self._show_status())),
        ]
        yield InlineCommandList(cmds, id="cmdlist")
        yield Input(placeholder="QUERY: company data / filings / financial analysis", id="prompt")
        yield Footer()

    async def on_mount(self) -> None:
        """Initialize the app when mounted."""
        # Hide command list initially
        self.query_one("#cmdlist", InlineCommandList).styles.display = "none"
        self.query_one("#prompt", Input).focus()
        # Start background consumer for stream events
        self.consumer_task = asyncio.create_task(self.consume_events())
        # Flush buffers at ~20 FPS (50ms)
        self.set_interval(0.05, self._flush_buffers)
        # Start status pulse animation
        self.set_interval(0.8, self._pulse_status)
        # REMOVED: Auto-refocus was preventing scrolling
        # self.set_interval(0.1, self._ensure_prompt_focus)

    def _ensure_prompt_focus(self) -> None:
        """Keep prompt focused unless user is in a modal or command list."""
        # Don't refocus if modal is open or command list is visible
        if self.screen.focused and self.screen.focused.id in ["olist", "prompt"]:
            return
        cl = self.query_one("#cmdlist", InlineCommandList)
        if cl.styles.display != "none":
            return
        # Auto-refocus prompt if focus drifted to Log
        try:
            prompt = self.query_one("#prompt", Input)
            if not prompt.disabled and self.focused != prompt:
                prompt.focus(scroll_visible=False)
        except Exception:
            pass

    async def on_unmount(self) -> None:
        """Clean up when app unmounts."""
        if self.consumer_task:
            self.consumer_task.cancel()

    def _set_status_running(self):
        """Update status to running."""
        status = self.query_one("#status-label", Label)
        status.update("▉ PROCESSING")
        status.remove_class("status-idle")
        status.add_class("status-running")

    def _set_status_idle(self):
        """Update status to idle."""
        status = self.query_one("#status-label", Label)
        status.update("▉ IDLE")
        status.remove_class("status-running")
        status.add_class("status-idle")

    def _pulse_status(self):
        """Pulse the status indicator when running."""
        label = self.query_one("#status-label", Label)
        if "status-running" in label.classes:
            self._pulse_on = not self._pulse_on
            # TRON-style pulse: bright/dim block
            if self._pulse_on:
                label.update(f"[{TRON_AMBER}]▉[/] PROCESSING")
            else:
                label.update(f"[dim {TRON_AMBER}]▉[/] PROCESSING")

    def _show_cmdlist(self):
        """Show the inline command list."""
        cl = self.query_one("#cmdlist", InlineCommandList)
        cl.styles.display = "block"

    def _hide_cmdlist(self):
        """Hide the inline command list."""
        cl = self.query_one("#cmdlist", InlineCommandList)
        cl.styles.display = "none"

    async def consume_events(self) -> None:
        """Consume streaming events from the agent runner and display them."""
        try:
            current_line = ""

            while True:
                try:
                    event: StreamEvent = await self.runner.queue.get()

                    if event.type == "thought_start":
                        if current_line:
                            self._append_msg(current_line + "\n")
                            current_line = ""
                        self._append_msg("Thinking...\n\n")

                    elif event.type == "thought_delta":
                        # Stream thought token by token as it arrives
                        self._append_msg(event.text)

                    elif event.type == "thought_end":
                        # End thought with newline
                        self._append_msg("\n")

                    elif event.type == "tool_call_start":
                        if current_line:
                            self._append_msg(current_line + "\n")
                            current_line = ""
                        self._append_msg(f"▸ {event.name}\n")

                    elif event.type == "tool_call_end":
                        if event.name == "web_search":
                            self._append_msg(f"  QUERY: {event.text}\n")

                    elif event.type == "tool_result":
                        self._append_msg(f"\t  ▸ {event.text}\n")

                    elif event.type == "message_delta":
                        current_line += event.text

                    elif event.type == "message_end":
                        if current_line:
                            self._append_msg(current_line + "\n")
                            current_line = ""

                    elif event.type == "completed":
                        if current_line:
                            self._append_msg(current_line + "\n")
                            current_line = ""
                        self._append_msg("▸ COMPLETED\n")
                        self.query_running = False
                        self._set_status_idle()

                except asyncio.CancelledError:
                    break
                except Exception as e:
                    if current_line:
                        self._append_msg(current_line + "\n")
                        current_line = ""
                    self._append_msg(f"Error: {e}\n")
                    self.query_running = False
                    self._set_status_idle()
        except asyncio.CancelledError:
            pass

    def on_input_changed(self, event: Input.Changed) -> None:
        """Show/hide and filter inline command list."""
        value = event.value.strip()
        cl = self.query_one("#cmdlist", InlineCommandList)
        if value.startswith("/"):
            matches = cl.filter(value)
            if matches > 0:
                self._show_cmdlist()
            else:
                self._hide_cmdlist()
        else:
            self._hide_cmdlist()

    def on_key(self, event: Key) -> None:
        """Handle key events."""
        cl = self.query_one("#cmdlist", InlineCommandList)
        if cl.styles.display != "none":
            # Command list is visible - handle navigation keys
            ol = cl.query_one(OptionList)
            if event.key == "up":
                ol.action_cursor_up()
                event.stop()
            elif event.key == "down":
                ol.action_cursor_down()
                event.stop()
            elif event.key == "pageup":
                ol.action_page_up()
                event.stop()
            elif event.key == "pagedown":
                ol.action_page_down()
                event.stop()
            elif event.key == "escape":
                self._hide_cmdlist()
                event.stop()

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        """Handle query submission or command execution."""
        value = event.value.strip()
        cl = self.query_one("#cmdlist", InlineCommandList)

        # If slash menu is visible and has a selection, execute it instead of querying
        if cl.styles.display != "none":
            cmd = cl.current_command()
            if cmd:
                event.input.value = ""      # clear prompt
                self._hide_cmdlist()        # hide menu
                cmd.run()                   # run command
                return

        # Normal query path
        query = value
        if not query:
            return

        # Clear input
        event.input.value = ""

        # Display user query
        self._append_msg(f"\n▸ QUERY: {query}\n")

        # Start agent execution
        self.query_running = True
        self._set_status_running()
        # Fire and forget - don't await
        asyncio.create_task(self.runner.ask(query))

    async def action_abort(self) -> None:
        """Abort the current agent execution."""
        if self.query_running:
            self.runner.cancel()
            self._append_msg("\n▸ ABORTED\n")
            self.query_running = False
            self._set_status_idle()

    async def action_clear(self) -> None:
        """Clear the output log."""
        self.query_one("#output", RichLog).clear()

    async def set_model(self, model: str) -> None:
        """Swap model live on the runner/agent."""
        self.runner.agent.client.model = model
        self._append_msg(f"\nMODEL SET: {model}\n")

    def _open_model_picker(self) -> None:
        """Open model picker modal."""
        models = ["gpt-5", "gpt-5-mini"]
        current = self.runner.agent.client.model

        def _picked(result: Optional[str]) -> None:
            if result:
                asyncio.create_task(self.set_model(result))

        self.push_screen(ModelPicker(models, current), _picked)

    async def _show_status(self) -> None:
        state = "PROCESSING" if self.query_running else "IDLE"
        self._append_msg(f"\nSTATUS: {state}  MODEL: {self.runner.agent.client.model}\n")


def launch_tui(edgar_user_agent: str, model: str = "gpt-5", max_iter: int = 20,
               reasoning_effort: str = "high", mode: AgentMode = AgentMode.FULL_NO_WEB,
               swallow_stdio: bool = False):
    """Launch the Textual TUI with the given agent configuration."""
    import sys
    import io

    # Redirect stdout/stderr by default to prevent corruption of TUI
    if swallow_stdio:
        sys.stdout = io.StringIO()
        sys.stderr = io.StringIO()

    try:
        agent = Agent(
            edgar_user_agent=edgar_user_agent,
            model=model,
            max_iter=max_iter,
            reasoning_effort=reasoning_effort,
            mode=mode
        )

        app = IntelliFinTUI(agent)
        app.run()
    except Exception as e:
        # Make sure errors are visible even if stdio was swallowed
        import sys
        print(f"Error launching TUI: {e}", file=sys.__stderr__)
        import traceback
        traceback.print_exc(file=sys.__stderr__)
        raise
