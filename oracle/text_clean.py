"""LaTeX / whitespace normaliser for the `text_clean` columns (exam_items, homework_items, homework_vec).

Spec: contracts/ledger-schema.sql (`text_clean` = "LaTeX-stripped text for BM25"), kit/03-architecture/data-model.md
("BM25 splits on non-alphanumerics and drops tokens over 40 bytes"), kit/04-tools/hotdata.md (same BM25 rules).

clean_text(raw)    -> readable normalised text: NFKC, math spans removed ($..$, $$..$$, \\(..\\), \\[..\\] and math
                      environments), \\commands removed (their brace text kept, except for non-prose commands such as
                      \\label or \\includegraphics whose argument is dropped), braces removed, line-break hyphenation
                      joined, whitespace collapsed, whitespace tokens longer than 40 UTF-8 bytes dropped.
                      Idempotent: the output never contains $ \\ { } ~ or control characters.
search_terms(text) -> the BM25 view of clean_text: alphanumeric runs only (split on everything else, as hotdata's
                      BM25 tokenizer does), runs over 40 bytes dropped, joined by single spaces, case kept (the
                      engine applies its own analyzer to query and document alike). Safe to embed in a single-quoted
                      SQL literal (see oracle.echo.search_text_literal).

CLI: python -m oracle.text_clean --text "..."   (or text on stdin) -> {"ok": true, "text_clean": ..., "search_terms": ...}
"""
from __future__ import annotations

import argparse
import re
import sys
import unicodedata

from oracle import config

MAX_TOKEN_BYTES = 40
_MATH_ENVS = "equation|align|alignat|gather|multline|eqnarray|displaymath|math|flalign"
_DROP_ARG_COMMANDS = ("begin", "end", "label", "ref", "eqref", "pageref", "cite", "includegraphics", "input",
                      "include", "url", "vspace", "hspace", "usepackage", "documentclass", "newcommand",
                      "renewcommand", "setlength", "pagestyle", "thispagestyle", "bibliography", "bibliographystyle")

_ENV_RE = re.compile(r"\\begin\{(" + _MATH_ENVS + r")(\*?)\}.*?\\end\{\1\2\}", re.S)
_DISPLAY_RE = re.compile(r"\$\$.*?\$\$|\\\[.*?\\\]|\\\(.*?\\\)", re.S)
_INLINE_RE = re.compile(r"\$[^$]*\$")
_DROP_ARG_RE = re.compile(r"\\(?:" + "|".join(_DROP_ARG_COMMANDS) + r")\*?(?:\[[^\]]*\])*(?:\{[^{}]*\})?")
_COMMAND_RE = re.compile(r"\\[A-Za-z@]+\*?(?:\[[^\]\n]*\])*")
_CONTROL_SYMBOL_RE = re.compile(r"\\.", re.S)
_HYPHEN_BREAK_RE = re.compile(r"(?<=[^\W\d_])-[ \t]*\n\s*(?=[^\W\d_])")
_ALNUM_RE = re.compile(r"[^\W_]+")


def _is_blank(raw: object) -> bool:
    if raw is None:
        return True
    try:
        return bool(raw != raw)  # NaN
    except TypeError:            # pandas.NA
        return True


def _drop_long(tokens: list[str]) -> list[str]:
    return [t for t in tokens if len(t.encode("utf-8")) <= MAX_TOKEN_BYTES]


def clean_text(raw: object) -> str:
    """Normalise one text for the `text_clean` column (module docstring). None / NaN / pandas.NA -> ''."""
    if _is_blank(raw):
        return ""
    text = unicodedata.normalize("NFKC", str(raw))
    text = "".join(" " if unicodedata.category(c) in ("Cc", "Cf") and c != "\n" else c for c in text)
    text = _HYPHEN_BREAK_RE.sub("", text)
    text = text.replace("\\$", " ")                     # an escaped dollar is not a math delimiter
    text = _ENV_RE.sub(" ", text)
    text = _DISPLAY_RE.sub(" ", text)
    text = _INLINE_RE.sub(" ", text)
    text = _DROP_ARG_RE.sub(" ", text)
    text = _COMMAND_RE.sub(" ", text)
    text = _CONTROL_SYMBOL_RE.sub(" ", text)            # \\, \, \& \% ... and any stray backslash
    text = re.sub(r"[{}]", "", text)
    text = re.sub(r"[$\\~]", " ", text)                 # unbalanced $, lone backslash, LaTeX non-breaking space
    return " ".join(_drop_long(text.split()))


def clean_or_none(raw: object) -> str | None:
    """clean_text() for the nullable ledger column: an empty result becomes None (NULL)."""
    return clean_text(raw) or None


def search_terms(text: object) -> str:
    """Alphanumeric tokens of clean_text(text), each at most 40 UTF-8 bytes, joined by single spaces."""
    return " ".join(_drop_long(_ALNUM_RE.findall(clean_text(text))))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m oracle.text_clean",
                                     description="Normalise one text (argument or stdin) and print one JSON object.")
    parser.add_argument("--text", help="text to clean (default: read stdin)")
    args = parser.parse_args(argv)
    raw = args.text if args.text is not None else sys.stdin.read()
    config.emit({"ok": True, "text_clean": clean_text(raw), "search_terms": search_terms(raw)})
    return config.EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
