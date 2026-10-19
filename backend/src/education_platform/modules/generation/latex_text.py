"""Wrap bare LaTeX in ``$...$`` so stored and displayed quiz math can render.

Already-delimited ``$...$`` and ``$$...$$`` spans are left unchanged. Existing
database rows are not rewritten; callers use this when saving a new item, and
the client normalizes older text at display time.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_STOP_WORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "by",
        "each",
        "equals",
        "find",
        "for",
        "from",
        "give",
        "gives",
        "how",
        "if",
        "in",
        "into",
        "is",
        "it",
        "its",
        "of",
        "on",
        "or",
        "over",
        "per",
        "than",
        "that",
        "the",
        "their",
        "then",
        "this",
        "to",
        "use",
        "what",
        "when",
        "where",
        "which",
        "whose",
        "with",
    }
)
_BINARY_COMMANDS = frozenset(
    {
        "approx",
        "cdot",
        "div",
        "equiv",
        "ge",
        "geq",
        "le",
        "leq",
        "mp",
        "ne",
        "neq",
        "pm",
        "times",
    }
)
_BINARY_OPS = frozenset("=+-*/(,")
_BARE_COMMAND = re.compile(
    r"\\(?:frac|dfrac|tfrac|times|div|sqrt|pi|text|cdot|le|ge|leq|geq|neq|ne)\b"
    r"|[\^_]\{"
)


@dataclass(frozen=True, slots=True)
class _Atom:
    start: int
    end: int
    kind: str


def _skip_brace(text: str, index: int) -> int:
    depth = 0
    cursor = index
    while cursor < len(text):
        char = text[cursor]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return cursor + 1
        cursor += 1
    return cursor


def _consume_scripts(text: str, index: int) -> int:
    cursor = index
    while cursor < len(text):
        char = text[cursor]
        if char == "{":
            cursor = _skip_brace(text, cursor)
            continue
        if char in "^_":
            cursor += 1
            if cursor < len(text) and text[cursor] == "{":
                cursor = _skip_brace(text, cursor)
                continue
            if cursor < len(text) and text[cursor].isalnum():
                cursor += 1
                continue
        break
    return cursor


def _tokenize(text: str) -> list[_Atom]:
    atoms: list[_Atom] = []
    index = 0
    length = len(text)
    while index < length:
        char = text[index]
        if char.isspace():
            start = index
            while index < length and text[index].isspace():
                index += 1
            atoms.append(_Atom(start, index, "space"))
            continue
        if char == "\\" and index + 1 < length and text[index + 1].isalpha():
            start = index
            index += 1
            while index < length and text[index].isalpha():
                index += 1
            index = _consume_scripts(text, index)
            atoms.append(_Atom(start, index, "latex"))
            continue
        if char == "\\" and index + 1 < length and text[index + 1] in ",;!:":
            atoms.append(_Atom(index, index + 2, "latex"))
            index += 2
            continue
        if char in "^_":
            start = index
            index = _consume_scripts(text, index)
            if index > start:
                atoms.append(_Atom(start, index, "latex"))
                continue
        if char in "=+-*/()[],":
            atoms.append(_Atom(index, index + 1, "op"))
            index += 1
            continue
        if char.isdigit():
            start = index
            while index < length and (text[index].isdigit() or text[index] == "."):
                index += 1
            atoms.append(_Atom(start, index, "num"))
            continue
        if char.isalpha():
            start = index
            while index < length:
                nxt = text[index]
                if nxt.isalnum():
                    index += 1
                    continue
                if nxt == "_" and (index + 1 >= length or text[index + 1] != "{"):
                    index += 1
                    continue
                break
            word = text[start:index]
            kind = (
                "ident"
                if len(word) == 1 or "_" in word or any(ch.isdigit() for ch in word)
                else "word"
            )
            atoms.append(_Atom(start, index, kind))
            continue
        atoms.append(_Atom(index, index + 1, "other"))
        index += 1
    return atoms


def _command_name(text: str, atom: _Atom) -> str:
    cursor = atom.start + 1
    start = cursor
    while cursor < atom.end and text[cursor].isalpha():
        cursor += 1
    return text[start:cursor]


def _neighbor(atoms: list[_Atom], index: int, step: int) -> _Atom | None:
    cursor = index + step
    while 0 <= cursor < len(atoms) and atoms[cursor].kind == "space":
        cursor += step
    if 0 <= cursor < len(atoms):
        return atoms[cursor]
    return None


def _is_binary(text: str, atom: _Atom | None) -> bool:
    if atom is None:
        return False
    if atom.kind == "op":
        return text[atom.start] in _BINARY_OPS
    if atom.kind != "latex":
        return False
    return _command_name(text, atom) in _BINARY_COMMANDS


def _can_take_word(text: str, atom: _Atom) -> bool:
    if atom.kind != "word":
        return False
    return text[atom.start : atom.end].lower() not in _STOP_WORDS


def _expand(atoms: list[_Atom], text: str, seed: int) -> tuple[int, int, int]:
    left = seed
    right = seed
    grew = True
    while grew:
        grew = False
        nxt = _neighbor(atoms, right, 1)
        if nxt is not None:
            next_index = atoms.index(nxt)
            if next_index > right and (
                nxt.kind in {"latex", "op", "num", "ident"}
                or (_can_take_word(text, nxt) and _is_binary(text, atoms[right]))
            ):
                right = next_index
                grew = True
        prev = _neighbor(atoms, left, -1)
        if prev is not None:
            prev_index = atoms.index(prev)
            toward = _neighbor(atoms, prev_index, 1)
            if prev_index < left and (
                prev.kind in {"latex", "op", "num", "ident"}
                or (_can_take_word(text, prev) and toward is not None and text[toward.start] == "=")
            ):
                left = prev_index
                grew = True
    return atoms[left].start, atoms[right].end, right


def _trim_core(text: str, start: int, end: int) -> tuple[int, int]:
    while start < end and text[start].isspace():
        start += 1
    while end > start and text[end - 1].isspace():
        end -= 1
    while end > start and text[end - 1] in ",.;:":
        end -= 1
    while end > start and text[end - 1].isspace():
        end -= 1
    return start, end


def _wrap_segment(segment: str) -> str:
    atoms = _tokenize(segment)
    out: list[str] = []
    cursor = 0
    index = 0
    while index < len(atoms):
        if atoms[index].kind != "latex":
            index += 1
            continue
        start, end, right = _expand(atoms, segment, index)
        core_start, core_end = _trim_core(segment, start, end)
        if core_end <= core_start:
            index += 1
            continue
        out.append(segment[cursor:core_start])
        out.append(f"${segment[core_start:core_end]}$")
        cursor = core_end
        index = right + 1
        while index < len(atoms) and atoms[index].end <= cursor:
            index += 1
    out.append(segment[cursor:])
    return "".join(out)


def _split_preserving_math(text: str) -> list[tuple[bool, str]]:
    parts: list[tuple[bool, str]] = []
    index = 0
    plain_start = 0
    length = len(text)
    while index < length:
        if text.startswith("$$", index):
            close = text.find("$$", index + 2)
            if close == -1:
                break
            if index > plain_start:
                parts.append((False, text[plain_start:index]))
            parts.append((True, text[index : close + 2]))
            index = close + 2
            plain_start = index
            continue
        if text[index] == "$":
            close = text.find("$", index + 1)
            if close == -1 or text[close - 1] == "\\":
                index += 1
                continue
            body = text[index + 1 : close]
            if "\n" in body:
                index += 1
                continue
            if index > plain_start:
                parts.append((False, text[plain_start:index]))
            parts.append((True, text[index : close + 1]))
            index = close + 1
            plain_start = index
            continue
        index += 1
    if plain_start < length:
        parts.append((False, text[plain_start:]))
    return parts


def normalize_quiz_math(text: str) -> str:
    """Wrap undelimited LaTeX commands. Delimited math is not wrapped again."""
    if not text or _BARE_COMMAND.search(text) is None:
        return text
    return "".join(
        part if is_math else _wrap_segment(part) for is_math, part in _split_preserving_math(text)
    )


def bare_latex_outside_delimiters(text: str) -> bool:
    """True when a known math command still sits outside ``$`` or ``$$``."""
    return any(
        not is_math and _BARE_COMMAND.search(part) for is_math, part in _split_preserving_math(text)
    )
