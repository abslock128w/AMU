"""Source-line boundaries for supported documentation structure, without execution."""

import re

from markdown_it import MarkdownIt

_ADORNMENT = re.compile(r"^([=\-~^\"'`:+*#<>_])\1{2,}$")
_LABEL = re.compile(r"^\.\. _[^:]+:\s*$")


def headings(text: str, suffix: str) -> tuple[list[dict], str | None]:
    lines = text.splitlines()
    rows = []
    problem = None
    if suffix in {".md", ".mdx"}:
        masked = list(lines)
        if lines and lines[0] in {"---", "+++"}:
            end = next((i for i in range(1, len(lines)) if lines[i] == lines[0]), None)
            if end is None:
                return [], "unclosed front matter"
            masked[:end + 1] = [""] * (end + 1)
        # CommonMark does not parse MDX expressions/JSX. Never approve a
        # replacement based on a guessed interpretation of those containers.
        if suffix == ".mdx" and re.search(r"[{}]|<[/A-Za-z]", text):
            problem = "MDX expressions/JSX require manual section editing"
        tokens = MarkdownIt("commonmark").parse("\n".join(masked))
        for i, token in enumerate(tokens):
            if token.type != "heading_open" or token.level != 0 or token.map is None:
                continue
            start, body = token.map
            rows.append({"start": start, "body": body, "boundary": start,
                         "level": int(token.tag[1:]), "title": tokens[i + 1].content,
                         "heading": lines[start].strip()})
        return rows, problem

    if suffix != ".rst":
        return [], "unsupported document format"
    styles = {}
    index = 0
    while index < len(lines):
        start = index
        line = lines[index]
        overline = bool(_ADORNMENT.fullmatch(line))
        title_index = index + 1 if overline else index
        if title_index + 1 >= len(lines):
            break
        title, underline = lines[title_index:title_index + 2]
        # RST directive/literal/quoted contents are indented, and therefore
        # not document section headings. Homogeneous adornments only.
        match = _ADORNMENT.fullmatch(underline)
        if (not title or title != title.lstrip() or title.startswith(".. ")
                or _ADORNMENT.fullmatch(title) or not match):
            index += 1
            continue
        if len(underline) < len(title.rstrip()) or (overline and line != underline):
            problem = "malformed RST heading adornment; edit manually"
            index += 1
            continue
        style = (overline, match[1])
        level = styles.setdefault(style, len(styles) + 1)
        boundary = start
        # Labels immediately preceding the heading belong to the next
        # section, not to the editable body of the previous section.
        scan = start - 1
        while scan >= 0 and (not lines[scan].strip() or _LABEL.fullmatch(lines[scan])):
            if _LABEL.fullmatch(lines[scan]):
                boundary = scan
            scan -= 1
        rows.append({"start": start, "body": title_index + 2, "boundary": boundary,
                     "level": level, "title": title.strip(), "heading": title.strip()})
        index = title_index + 2
    return rows, problem
