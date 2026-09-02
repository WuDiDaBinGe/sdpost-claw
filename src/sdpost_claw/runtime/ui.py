"""Terminal UI - Rich-based terminal interface."""

from __future__ import annotations

from typing import Any

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich.markdown import Markdown
from rich.live import Live
from rich.layout import Layout
from rich.syntax import Syntax

console = Console()


class TerminalUI:
    """
    Terminal UI - Rich-based interactive terminal interface.

    Features:
    - Streaming output
    - Markdown rendering
    - Syntax highlighting
    - Progress indicators
    - Session status display
    """

    def __init__(self):
        self.console = Console()

    def print(self, *args, **kwargs) -> None:
        """Print to console."""
        self.console.print(*args, **kwargs)

    def print_markdown(self, text: str) -> None:
        """Print markdown text."""
        self.console.print(Markdown(text))

    def print_code(self, code: str, language: str = "python") -> None:
        """Print syntax-highlighted code."""
        syntax = Syntax(code, language, theme="monokai")
        self.console.print(syntax)

    def print_panel(
        self,
        content: Any,
        title: str | None = None,
        style: str = "blue",
    ) -> None:
        """Print content in a panel."""
        self.console.print(Panel(content, title=title, border_style=style))

    def print_table(
        self,
        headers: list[str],
        rows: list[list[Any]],
        title: str | None = None,
    ) -> None:
        """Print a table."""
        table = Table(title=title)
        for header in headers:
            table.add_column(header)
        for row in rows:
            table.add_row(*[str(cell) for cell in row])
        self.console.print(table)

    def print_success(self, message: str) -> None:
        """Print success message."""
        self.console.print(f"[bold green]✓[/bold green] {message}")

    def print_error(self, message: str) -> None:
        """Print error message."""
        self.console.print(f"[bold red]✗[/bold red] {message}")

    def print_warning(self, message: str) -> None:
        """Print warning message."""
        self.console.print(f"[bold yellow]⚠[/bold yellow] {message}")

    def print_info(self, message: str) -> None:
        """Print info message."""
        self.console.print(f"[bold blue]ℹ[/bold blue] {message}")

    def print_stream_token(self, token: str) -> None:
        """Print a streaming token."""
        self.console.print(token, end="", soft_wrap=True)

    def print_divider(self, title: str = "") -> None:
        """Print a divider line."""
        self.console.rule(title)

    def print_session_header(self, session: Any) -> None:
        """Print session header."""
        info = Text()
        info.append(f"Session: ", style="bold")
        info.append(session.title, style="cyan")
        info.append(f"  [{session.agent_mode}]", style="green")
        info.append(f"  ({session.id[:8]}...)", style="dim")
        self.console.print(info)

    def print_tool_call(self, tool_name: str, tool_input: dict) -> None:
        """Print tool call info."""
        self.console.print(f"[dim]→ Calling [bold]{tool_name}[/bold]...[/dim]")

    def print_tool_result(self, tool_name: str, result: str, is_error: bool = False) -> None:
        """Print tool result."""
        style = "red" if is_error else "green"
        preview = result[:100] + "..." if len(result) > 100 else result
        self.console.print(f"  [{style}]← {tool_name}: {preview}[/{style}]")

    def print_welcome(self) -> None:
        """Print welcome banner."""
        banner = """
╔═══════════════════════════════════════════════════════════════╗
║                                                               ║
║   🐾  sdpost-claw v0.1.0                                     ║
║      Open-source AI Office Agent Desktop Workbench            ║
║                                                               ║
║   Type your task or 'help' for commands.                      ║
║   Type 'quit' or 'exit' to quit.                             ║
║                                                               ║
╚═══════════════════════════════════════════════════════════════╝
        """
        self.console.print(banner, style="bold blue")

    def print_help(self) -> None:
        """Print help information."""
        help_text = """
## Commands

- `help` - Show this help
- `quit` / `exit` - Exit sdpost-claw
- `new` - Create new session
- `list` - List sessions
- `resume <id>` - Resume a session
- `mode <build|plan|general>` - Switch agent mode
- `clear` - Clear screen

## Usage

Just type your task in natural language:
- "Analyze the data in report.xlsx"
- "Create a presentation about Q3 results"
- "Review the code in src/main.py"
- "Write a summary of the meeting notes"
        """
        self.print_markdown(help_text)

    def input_prompt(self, session_title: str = "sdpost") -> str:
        """Get user input (rich markup rendered by console.input)."""
        try:
            return self.console.input(f"\n[bold cyan]{session_title}[/bold cyan] > ")
        except (EOFError, KeyboardInterrupt):
            return "exit"

    def confirm(self, message: str) -> bool:
        """Ask for confirmation."""
        response = input(f"{message} [y/N]: ").strip().lower()
        return response in ("y", "yes")

    def select_option(self, message: str, options: list[str]) -> int:
        """Let user select an option."""
        self.console.print(f"\n{message}")
        for i, opt in enumerate(options, 1):
            self.console.print(f"  [bold]{i}[/bold]. {opt}")
        while True:
            try:
                choice = int(input("Select: ").strip())
                if 1 <= choice <= len(options):
                    return choice - 1
            except ValueError:
                pass
            self.console.print("[red]Invalid choice[/red]")
