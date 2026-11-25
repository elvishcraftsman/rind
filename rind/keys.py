"""Keyboard shortcuts: a stable id for every binding, and [keys] overrides from config.toml.

The id of a binding is its action name, e.g. "cut_files" or "goto_tab_3". In config.toml:

    [keys]
    cut_files = "ctrl+x"             # replaces the default keys for that action
    copy_files = ["c", "ctrl+c"]     # several keys
    delete_items = ""                # an empty value turns the shortcut off

`rind --keys` lists every id with its current keys.
"""
from __future__ import annotations

import re
from dataclasses import replace
from typing import Iterable

from textual.binding import Binding, BindingsMap

# Single punctuation characters, written the way Textual names them.
PUNCT = {"/": "slash", ":": "colon", ".": "full_stop", ",": "comma", "?": "question_mark",
         "!": "exclamation_mark", "+": "plus", "-": "minus", "=": "equals_sign", "*": "asterisk",
         "_": "underscore", "~": "tilde", "|": "vertical_line", "\\": "backslash", ";": "semicolon",
         "'": "apostrophe", '"': "quotation_mark", "#": "number_sign", "@": "at", "%": "percent_sign",
         "&": "ampersand", "$": "dollar_sign", "^": "circumflex_accent", "`": "grave_accent",
         "(": "left_parenthesis", ")": "right_parenthesis", "[": "left_square_bracket",
         "]": "right_square_bracket", "{": "left_curly_bracket", "}": "right_curly_bracket",
         "<": "less_than_sign", ">": "greater_than_sign"}
ALIASES = {"esc": "escape", "return": "enter", "del": "delete", "pgup": "pageup", "pgdn": "pagedown",
           "pgdown": "pagedown", "ctl": "ctrl", "control": "ctrl", "option": "alt", "meta": "alt"}


def binding_id(b: Binding) -> str:
    act = b.action.removeprefix("app.")
    m = re.fullmatch(r"goto_tab\((\d+)\)", act)
    if m:
        return f"goto_tab_{int(m.group(1)) + 1}"
    return re.sub(r"\W+", "_", act).strip("_")


def with_ids(bindings: Iterable[Binding]) -> list[Binding]:
    return [b if b.id else replace(b, id=binding_id(b)) for b in bindings]


def normalize_key(s: str) -> str:
    """'Ctrl+X' -> 'ctrl+x', 'Shift+z' -> 'Z', '/' -> 'slash', 'PgDn' -> 'pagedown'."""
    s = s.strip()
    if not s:
        return ""
    parts = s[:-2].split("+") + ["plus"] if s.endswith("++") and len(s) > 2 else s.split("+")
    last = parts[-1]
    mods = [ALIASES.get(p.strip().lower(), p.strip().lower()) for p in parts[:-1]]
    if len(last) == 1:
        last = PUNCT.get(last, last)
    else:
        last = ALIASES.get(last.lower(), last.lower())
    if mods == ["shift"] and len(last) == 1 and last.isalpha():
        return last.upper()
    if mods and len(last) == 1:
        last = last.lower()
    return "+".join(mods + [last])


def configure(raw: dict, known_ids: set[str]) -> tuple[dict[str, list[str]], list[str]]:
    """Validate the [keys] table. Returns ({id: [keys]}, [error messages]); [] means 'disabled'."""
    out: dict[str, list[str]] = {}
    errors: list[str] = []
    for name, val in raw.items():
        ident = str(name).strip().replace("-", "_")
        if ident not in known_ids:
            errors.append(f"[keys] unknown action '{name}' (run 'rind --keys' for the list)")
            continue
        if isinstance(val, str):
            items = val.split(",") if val.strip() not in ("+", ",") else [val.strip()]
        elif isinstance(val, list) and all(isinstance(v, str) for v in val):
            items = val
        else:
            errors.append(f"[keys] {name}: use a string or a list of strings")
            continue
        out[ident] = [k for k in (normalize_key(i) for i in items) if k]
    return out, errors


def apply(bmap: BindingsMap, overrides: dict[str, list[str]]) -> None:
    """Rewrite bmap in place: each overridden id gets exactly the configured keys.
    A key that is given to one action is taken away from whatever else used it."""
    if not overrides:
        return
    templates: dict[str, Binding] = {}
    for bl in bmap.key_to_bindings.values():
        for b in bl:
            if b.id in overrides:
                templates.setdefault(b.id, b)
    taken = {k for keys in overrides.values() for k in keys}
    for key in list(bmap.key_to_bindings):
        kept = [b for b in bmap.key_to_bindings[key] if b.id not in overrides and key not in taken]
        if kept:
            bmap.key_to_bindings[key] = kept
        else:
            del bmap.key_to_bindings[key]
    for ident, keys in overrides.items():
        tmpl = templates.get(ident)
        if tmpl is None:
            continue
        for k in keys:
            bmap.key_to_bindings.setdefault(k, []).append(tmpl.with_key(k, None))


def effective(bindings: Iterable[Binding], overrides: dict[str, list[str]]) -> dict[str, tuple[str, list[str]]]:
    """{id: (description, [keys])} after overrides, in definition order."""
    out: dict[str, tuple[str, list[str]]] = {}
    for b in bindings:
        if not b.id:
            continue
        desc, keys = out.get(b.id, (b.description, []))
        out[b.id] = (desc, keys + [k for k in b.key.split(",") if k not in keys])
    for ident, keys in overrides.items():
        if ident in out:
            out[ident] = (out[ident][0], list(keys))
    return out
