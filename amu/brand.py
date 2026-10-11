"""TWC Thugs branding — gold identity. Everything here goes to stderr and is never emitted under --json.
Colour is always redundant with a glyph or a word, so NO_COLOR=1 reads the same."""

from __future__ import annotations

import os
import sys

from rich.console import Console
from rich.rule import Rule
from rich.text import Text
from rich.theme import Theme

NAME = "TWC Thugs"
TAGLINE = "amu · Anti-Messup · built on the Entire CLI"
EPILOG = "Built by TWC Thugs for the Bengaluru Tech Week Buildathon 2026"
PROMPT = "twc-thugs ❯ "

PALETTE = {
    "gold": "#F5B32E",
    "amber": "#E08A1E",
    "ember": "#C2410C",
    "sand": "#FDF6E3",
    "ash": "#8A8578",
    "bg": "#161412",
}
# Confidence / risk colours are semantic and never gold.
SEMANTIC = {"sound": "#3CB371", "guessed": PALETTE["amber"], "unknown": PALETTE["ash"], "risk": PALETTE["ember"]}

THEME = Theme(
    {
        "brand": f"bold {PALETTE['gold']}",
        "brand.sub": PALETTE["ash"],
        "brand.rule": PALETTE["amber"],
        "brand.caret": PALETTE["amber"],
        "brand.body": PALETTE["sand"],
        "brand.muted": PALETTE["ash"],
        "brand.label": f"{PALETTE['ash']}",
        "conf.sound": SEMANTIC["sound"],
        "conf.guessed": SEMANTIC["guessed"],
        "conf.unknown": SEMANTIC["unknown"],
        "risk": f"bold {SEMANTIC['risk']}",
        "state.green": SEMANTIC["sound"],
        "state.red": SEMANTIC["risk"],
        "state.editing": PALETTE["gold"],
        "state.drift": SEMANTIC["risk"],
        "state.planned": PALETTE["ash"],
    }
)

# pyfiglet 'slant' rendering of "TWC THUGS", pasted so pyfiglet is not a runtime dependency.
WORDMARK = r"""
  ______ _       __ ______   ______ __  __ __  __ ______ _____
 /_  __/| |     / // ____/  /_  __// / / // / / // ____// ___/
  / /   | | /| / // /        / /  / /_/ // / / // / __  \__ \
 / /    | |/ |/ // /___     / /  / __  // /_/ // /_/ / ___/ /
/_/     |__/|__/ \____/    /_/  /_/ /_/ \____/ \____/ /____/
""".strip("\n")


def color_enabled() -> bool:
    return not os.environ.get("NO_COLOR") and sys.stderr.isatty()


def err_console() -> Console:
    """stderr console; Rich degrades truecolor → 256 → 16 → none and honours NO_COLOR by itself."""
    return Console(
        stderr=True, theme=THEME, highlight=False, soft_wrap=False, no_color=bool(os.environ.get("NO_COLOR"))
    )


def out_console() -> Console:
    return Console(theme=THEME, highlight=False, soft_wrap=False, no_color=bool(os.environ.get("NO_COLOR")))


def header(command: str, repo: str, json_mode: bool = False) -> None:
    """One-line header + a single amber rule, printed before every non-JSON command."""
    if json_mode:
        return
    c = err_console()
    c.print(
        Text.assemble(
            (NAME, "brand"), (" · amu ", "brand.body"), (command, "brand"), (" · ", "brand.body"), (repo, "brand.sub")
        )
    )
    c.print(Rule(style="brand.rule"))


def banner(version: str, repo: str, entire_status: str, json_mode: bool = False) -> None:
    if json_mode:
        return
    c = err_console()
    c.print(Text(WORDMARK, style="brand"))
    c.print(Text(f"  {TAGLINE}", style="brand.sub"))
    c.print(
        Text.assemble(
            ("  v" + version, "brand.body"),
            (" · ", "brand.sub"),
            (repo, "brand"),
            (" · entire ", "brand.sub"),
            (entire_status, "brand.body"),
        )
    )
    c.print(Rule(style="brand.rule"))


def prompt_text() -> Text:
    return Text.assemble(("twc-thugs ", "brand"), ("❯ ", "brand.caret"))


def status_line(phase: str, spend: str, open_questions: int) -> str:
    return f"{NAME} · phase {phase} · spend {spend} · open questions {open_questions}"


class _Quiet:
    def __enter__(self):
        return self

    def update(self, text: str) -> None:
        pass

    def __exit__(self, *exc):
        return False


class _Spinner:
    """Progress that names the real work (counts come from the graph). Silent when stderr is not a terminal."""

    def __init__(self, text: str):
        self._status = err_console().status(f"[brand.muted]{text}[/]", spinner="dots", spinner_style="brand.rule")

    def __enter__(self):
        self._status.__enter__()
        return self

    def update(self, text: str) -> None:
        self._status.update(f"[brand.muted]{text}[/]")

    def __exit__(self, *exc):
        return self._status.__exit__(*exc)


def progress(text: str):
    return _Spinner(text) if sys.stderr.isatty() and not os.environ.get("NO_COLOR") else _Quiet()
