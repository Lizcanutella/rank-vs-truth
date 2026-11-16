"""Reply parser for the format
    answer: <A|B|C|D, or FLAG ...> / confidence: <high|medium|low> / comment: <text>

Keywords are matched case-insensitively. A field line without the colon counts only if the value ends
the line or is followed by ) ] . : , or a space and "(" or a dash, so prose such as "Answer A is wrong"
is not read as a field. When a reply has several lines of one field (a self-correction), the last one
wins, for each field separately.
"""
from __future__ import annotations

import re

from .prompts import FLAG, LETTERS

_LINE = re.compile(r"(?im)^[^\w\n]*(answer|confidence|comment)\b([^\n]*)$")
_BARE_LETTER = re.compile(r"^[^\w\n]*([A-Da-d])(?=[^\S\n]*$|[)\].:,]|[^\S\n]+[(\-–—])")
_COLON_LETTER = re.compile(r"^[^\w\n]*([A-Da-d])\b")
_LEVEL = re.compile(r"^[^\w\n]*(high|medium|low)\b", re.I)
# A bracketed tag that opens a field line, e.g. "(private note) answer: C" or "**[Private note]** answer: B".
_TAG = re.compile(r"(?im)^[^\w\n]*[(\[][^)\]\n]{1,40}[)\]][^\w\n]*(?=(?:answer|confidence|comment)\b)")


def strip_think(text: str | None) -> str:
    return re.sub(r"<think>.*?</think>", "", text or "", flags=re.S).strip()


def fields(text: str) -> dict[str, list[dict]]:
    """Every field line in reply order: {name: [{"pos", "colon", "value", "letter"?, "level"?}]}."""
    out: dict[str, list[dict]] = {"answer": [], "confidence": [], "comment": []}
    for m in _LINE.finditer(strip_think(text)):
        name, tail = m.group(1).lower(), m.group(2)
        head = re.match(r"[^\w\n]*", tail).group(0)
        colon = ":" in head
        value = tail[tail.index(":") + 1:] if colon else tail
        f = {"pos": m.start(), "colon": colon, "value": value.strip()}
        if name == "answer":
            lm = (_COLON_LETTER if colon else _BARE_LETTER).match(value)
            if lm:
                f["letter"] = lm.group(1).upper()
            elif re.match(r"^[^\w\n]*flag\b" if colon else r"^[^\w\n]*flag(?=[^\S\n]*$|[)\].:,]|[^\S\n]+[(\-–—])",
                          value, re.I):
                f["letter"] = FLAG
            else:
                continue
        elif name == "confidence":
            lm = _LEVEL.match(value)
            if not lm:
                continue
            f["level"] = lm.group(1).lower()
        elif not colon and value and not value[0].isspace():
            continue
        out[name].append(f)
    return out


def parse_strict(text: str | None) -> dict:
    """{'answer': 'A'-'D' | 'FLAG' | None, 'confidence', 'comment'}; the last line of each field wins.
    Used for the screening samples."""
    if text is None:
        return {"answer": None, "confidence": None, "comment": None}
    fs = fields(text)
    a, c, n = (fs[k][-1] if fs[k] else None for k in ("answer", "confidence", "comment"))
    return {"answer": a["letter"] if a else None, "confidence": c["level"] if c else None,
            "comment": n["value"] if n else None}


def parse_reply(text: str | None) -> dict:
    """parse_strict after deleting a bracketed tag that opens a field line. Used for session replies
    (entries, private notes, fillers) and in the analysis."""
    return parse_strict(None if text is None else _TAG.sub("", text))


def answer_text(options: list[str], a: str | None) -> str | None:
    """The option text of a letter answer; FLAG and None unchanged."""
    return options[LETTERS.index(a)] if a is not None and a in LETTERS else a
