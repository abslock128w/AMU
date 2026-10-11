"""Visual-regression coverage for the interactive command guide."""

from __future__ import annotations

from io import StringIO

from rich.console import Console

from amu import cli, router


def _render(monkeypatch, printer, width: int = 52) -> str:
    stream = StringIO()
    console = Console(
        file=stream, width=width, theme=cli.brand.THEME, no_color=True, color_system=None, highlight=False
    )
    monkeypatch.setattr(cli.brand, "err_console", lambda: console)
    printer()
    return stream.getvalue()


def test_help_is_structured_and_fits_a_narrow_terminal(monkeypatch):
    output = _render(monkeypatch, cli.print_help)

    assert "AMU command guide" in output
    assert "Start here" in output
    assert "Explore" in output
    assert "Plan a change" in output
    assert "Workspace" in output
    for command in router.SLASH:
        assert command in output
    assert max(len(line.rstrip()) for line in output.splitlines()) <= 52


def test_start_hint_and_unknown_command_are_compact(monkeypatch):
    start = _render(monkeypatch, cli.print_start_hint)
    unknown = _render(monkeypatch, lambda: cli.print_command_error("wat"))

    assert "Start: /tree" in start
    assert "AMU command guide" not in start
    assert "Not a command: /wat" in unknown
    assert "AMU command guide" not in unknown
    assert "Type /help" in unknown


def test_read_line_refreshes_completion_without_recreating_the_prompt_session(monkeypatch):
    class Session:
        completer = None

        def __init__(self):
            self.prompts = []

        def prompt(self, label):
            self.prompts.append(label)
            return "/help"

    session = Session()
    monkeypatch.setattr(cli, "_completer", lambda repo: f"completer:{repo}")
    monkeypatch.setattr(cli, "_prompt_label", lambda: "prompt")

    assert cli._read_line("first-repo", session) == "/help"
    assert cli._read_line("second-repo", session) == "/help"
    assert session.completer == "completer:second-repo"
    assert session.prompts == ["prompt", "prompt"]
