from __future__ import annotations

import re
import shlex
from collections.abc import Mapping

_SHELL_PUNCTUATION = ";&|<>"
_PLACEHOLDER = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")


def render_command_argv(template: str, values: Mapping[str, object]) -> list[str]:
    """Render a configured local command template into argv without invoking a shell.

    Templates may quote normal arguments and use ``{name}`` placeholders. Shell control
    operators (pipes, redirects, command separators/backgrounding) are rejected when they occur
    as shell syntax. If an integration genuinely needs a pipeline, operators must configure a
    reviewed wrapper executable and point the template at that executable instead.

    Placeholder values are substituted *after* tokenization, so characters supplied by a file
    path, transcript or other runtime value can never become shell syntax or additional argv
    elements.
    """
    raw = str(template or "").strip()
    if not raw:
        raise ValueError("Configured command template is empty")
    if "\x00" in raw or "\n" in raw or "\r" in raw:
        raise ValueError("Configured command template contains invalid control characters")

    lexer = shlex.shlex(raw, posix=True, punctuation_chars=_SHELL_PUNCTUATION)
    lexer.whitespace_split = True
    lexer.commenters = ""
    try:
        tokens = list(lexer)
    except ValueError as exc:
        raise ValueError("Configured command template has invalid quoting") from exc

    if not tokens:
        raise ValueError("Configured command template is empty")

    for token in tokens:
        if token and all(char in _SHELL_PUNCTUATION for char in token):
            raise ValueError(
                "Configured command template may not use shell pipes, redirects or control operators; "
                "use a reviewed wrapper executable instead"
            )

    string_values = {str(key): str(value) for key, value in values.items()}
    rendered: list[str] = []
    for token in tokens:
        placeholders = sorted(set(_PLACEHOLDER.findall(token)))
        unsupported = [name for name in placeholders if name not in string_values]
        if unsupported:
            raise ValueError(
                "Configured command template contains unsupported placeholders: "
                + ", ".join(unsupported)
            )

        current = token
        for key, value in string_values.items():
            current = current.replace("{" + key + "}", value)
        if "\x00" in current:
            raise ValueError("Configured command argument contains a NUL byte")
        rendered.append(current)

    if not rendered[0].strip():
        raise ValueError("Configured command template has no executable")
    return rendered


__all__ = ["render_command_argv"]
