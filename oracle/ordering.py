"""The time-ordering rule: which rows a prediction for target exam T may see.

Spec: contracts/README.md "The time-ordering rule" (overrides the date-based wording in the kit):
    term_seq = year*10 + season        (1 = spring 'S', 2 = summer 'U', 3 = fall 'F'; '2011F' -> 20113)
    visible(row, T)  <=>  row.term_seq < T.term_seq
                          OR (row.term_seq = T.term_seq AND row.session < T.session)
Dates are informational only. A NULL/unknown session means "end of term" (ledger-schema.sql, exams.session),
so such a row is visible only from a later term, and an end-of-term target sees every numbered session of
its own term. feature_set is 'full' only when T is in the course's published term, otherwise
'exam_history' (x2..x6 = 0: the published term's lectures and psets lie in T's future).
"""
from __future__ import annotations

import re
from typing import Literal

SEASONS: dict[str, int] = {"S": 1, "U": 2, "F": 3}
_SEASON_LETTER = {number: letter for letter, number in SEASONS.items()}
END_OF_TERM = 1_000_000  # session used for NULL / unknown sessions; sorts after every real session
EXAM_HISTORY_ZERO_SIGNALS = ("x2", "x3", "x4", "x5", "x6")
FeatureSet = Literal["full", "exam_history"]

_TERM_RE = re.compile(r"(\d{4})([SUF])")
_IDENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def term_to_seq(term: str) -> int:
    """'2011F' -> 20113, '2009S' -> 20091, '2010U' -> 20102 (case-insensitive). ValueError otherwise."""
    match = _TERM_RE.fullmatch(term.strip().upper()) if isinstance(term, str) else None
    if not match:
        raise ValueError(f"invalid term {term!r}: expected YYYY + S|U|F, e.g. '2011F'")
    return int(match.group(1)) * 10 + SEASONS[match.group(2)]


def as_term_seq(value: int | str) -> int:
    """Accept a term string ('2011F') or a term_seq (20113) and return the validated term_seq."""
    if isinstance(value, str):
        return term_to_seq(value)
    if isinstance(value, bool):
        raise ValueError(f"invalid term_seq {value!r}")
    seq = int(value)
    if seq % 10 not in _SEASON_LETTER or not 1000 <= seq // 10 <= 9999:
        raise ValueError(f"invalid term_seq {value!r}: expected year*10 + 1|2|3, e.g. 20113")
    return seq


def seq_to_term(term_seq: int) -> str:
    """20113 -> '2011F' (inverse of term_to_seq)."""
    year, season = divmod(as_term_seq(term_seq), 10)
    return f"{year}{_SEASON_LETTER[season]}"


def session_or_end(session: object) -> int:
    """The session number, or END_OF_TERM for None / NaN / pandas.NA (unknown session = end of term)."""
    if session is None:
        return END_OF_TERM
    try:
        if session != session:  # NaN
            return END_OF_TERM
    except TypeError:  # pandas.NA has no truth value
        return END_OF_TERM
    return int(session)  # type: ignore[call-overload]


def visible(row_term_seq: int | str, row_session: int | None,
            target_term_seq: int | str, target_session: int | None) -> bool:
    """The visibility rule for one row (contracts/README.md)."""
    row_t, target_t = as_term_seq(row_term_seq), as_term_seq(target_term_seq)
    if row_t != target_t:
        return row_t < target_t
    return session_or_end(row_session) < session_or_end(target_session)


def visible_sql(term_seq_col: str = "term_seq", session_col: str = "session", *, alias: str | None = None,
                target_term_seq_param: str = "target_term_seq", target_session_param: str = "target_session") -> str:
    """SQL boolean fragment for the visibility rule with named parameters, e.g.
    ("term_seq" < $target_term_seq OR ("term_seq" = $target_term_seq AND COALESCE("session", 1000000) < $target_session))

    Bind with visibility_params(). Runs on DuckDB (native $name binding) and on hotdata (oracle.backend renders
    the parameters as validated literals). Only identifiers are interpolated, and they are validated."""
    for ident in (term_seq_col, session_col, target_term_seq_param, target_session_param, *([alias] if alias else [])):
        if not _IDENT_RE.fullmatch(ident):
            raise ValueError(f"invalid SQL identifier {ident!r}")
    prefix = f"{alias}." if alias else ""
    term, session = f'{prefix}"{term_seq_col}"', f'{prefix}"{session_col}"'
    return (f"({term} < ${target_term_seq_param} OR ({term} = ${target_term_seq_param} "
            f"AND COALESCE({session}, {END_OF_TERM}) < ${target_session_param}))")


def visibility_params(target_term_seq: int | str, target_session: int | None, *,
                      target_term_seq_param: str = "target_term_seq",
                      target_session_param: str = "target_session") -> dict[str, int]:
    """Parameters for visible_sql(); an unknown target session becomes END_OF_TERM."""
    return {target_term_seq_param: as_term_seq(target_term_seq), target_session_param: session_or_end(target_session)}


def feature_set(target_term_seq: int | str, published_term_seq: int | str) -> FeatureSet:
    """'full' if the target exam is in the course's published term, else 'exam_history' (contracts/README.md)."""
    return "full" if as_term_seq(target_term_seq) == as_term_seq(published_term_seq) else "exam_history"
