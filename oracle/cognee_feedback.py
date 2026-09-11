"""Turn a scored backtest (or a student's rating) into Cognee feedback: session.add_feedback(...) then improve().

Spec: kit/03-architecture/rote-plays.md (cognee_feedback.py: run session + score -> add_feedback + improve();
degraded on failure); kit/04-tools/cognee.md step 5 (backtests are sessions, session_id "run-<n>"; after scoring,
add_feedback with a score derived from points covered; then improve(session_ids=[...])); contracts/student.md §3
(student feedback -> add_feedback(session_id, qa_id, feedback_text, feedback_score) then improve); contracts/README.md
(feedback is cognee.session.add_feedback — there is no top-level cognee.add_feedback in 1.5.4).

Verified signatures (cognee 1.5.4): session.get_session(session_id=None, last_n=None, user=None) -> [SessionQAEntry];
session.add_feedback(session_id, qa_id, feedback_text=None, feedback_score=None, user=None) -> bool;
improve(dataset="main_dataset", *, session_ids=None, ..., **{user, feedback_alpha, ...}).

Without LLM_API_KEY nothing is sent: exit 0 with {"ok": true, "skipped": "no LLM_API_KEY", ...}. A failure of the
live call is degraded, never hard: exit 0 with {"ok": true, "warning": ...}. Usage errors exit 1.
"""
from __future__ import annotations

import asyncio
import math
import re
import sys
from typing import Any, Sequence

from oracle import config
from oracle._cli import ScriptParser
from oracle.backend import get_backend
from oracle.cognee_tag import course_dataset_name

SKIPPED_NO_KEY = "no LLM_API_KEY"
MAX_TEXT_CHARS = 2000
RUN_SQL = "SELECT run_id, run_seq, course, model_pts FROM {{runs}} WHERE run_id = $run_id"
_RUN_ID_RE = re.compile(r"r([0-9]+)-.+")


def score_from_points(model_pts: float) -> int:
    """Feedback score 1..5 from the share of exam points the top-K covered (0..1): 1 + round(4 * share)."""
    if isinstance(model_pts, bool) or not isinstance(model_pts, (int, float)) or not math.isfinite(model_pts):
        raise ValueError(f"model_pts must be a number, got {model_pts!r}")
    return 1 + round(4 * min(1.0, max(0.0, float(model_pts))))


def session_id_for_run(run_id: str) -> str:
    """'run-<run_seq>' for run_id 'r<seq>-<exam_id>' (kit/04-tools/cognee.md: session_id=f"run-{n}")."""
    match = _RUN_ID_RE.fullmatch(config.validate_id(run_id, what="run_id"))
    if not match:
        raise ValueError(f"run_id {run_id!r} is not of the form r<seq>-<exam_id>")
    return f"run-{int(match.group(1))}"


def run_feedback_inputs(run_id: str, backend: Any | None = None) -> dict[str, Any]:
    """The run's course and model_pts from the ledger `runs` row. LookupError if absent or not scored yet."""
    be = backend if backend is not None else get_backend()
    frame = be.query(be.ledger(), RUN_SQL, {"run_id": config.validate_id(run_id, what="run_id")})
    if frame.empty:
        raise LookupError(f"run {run_id} is not in the ledger `runs` table")
    row = frame.to_dict("records")[0]
    pts = row["model_pts"]
    if pts is None or (isinstance(pts, float) and math.isnan(pts)):
        raise LookupError(f"run {run_id} has no model_pts yet (score.py has not written it)")
    return {"run_id": run_id, "run_seq": int(row["run_seq"]), "course": str(row["course"]), "model_pts": float(pts)}


def _check_inputs(session_id: str, dataset: str, score: int | None, text: str | None,
                  qa_ids: Sequence[str] | None) -> None:
    config.validate_id(session_id, what="session_id")
    if "." in config.validate_id(dataset, what="dataset"):
        raise ValueError(f"cognee dataset names cannot contain dots: {dataset!r}")
    if score is not None and (isinstance(score, bool) or not isinstance(score, int) or not 1 <= score <= 5):
        raise ValueError(f"score must be an integer 1..5, got {score!r}")
    if text is not None and (not isinstance(text, str) or len(text) > MAX_TEXT_CHARS):
        raise ValueError(f"text must be a string of at most {MAX_TEXT_CHARS} characters")
    for qa in qa_ids or ():
        if not isinstance(qa, str) or not qa or len(qa) > 200:
            raise ValueError(f"bad qa_id {qa!r}")


async def apply_feedback_async(session_id: str, *, dataset: str, score: int | None = None, text: str | None = None,
                               qa_ids: Sequence[str] | None = None, user: Any | None = None,
                               cognee_module: Any | None = None) -> dict[str, Any]:
    """Rate the session's answers, then improve() the dataset with that session. Offline no-op without
    LLM_API_KEY ({"skipped": "no LLM_API_KEY"}). When qa_ids is empty every Q&A entry of the session is rated.
    `cognee_module` replaces the imported cognee (tests pass a fake bound to the real signatures)."""
    _check_inputs(session_id, dataset, score, text, qa_ids)
    if not config.env("LLM_API_KEY"):
        return {"skipped": SKIPPED_NO_KEY}
    if cognee_module is None:
        from oracle.models import import_cognee

        cognee_module = import_cognee()
    cognee = cognee_module
    user_kw = {"user": user} if user is not None else {}
    if qa_ids:
        targets = list(qa_ids)
    else:
        entries = await cognee.session.get_session(session_id=session_id, **user_kw)  # LIVE [TEST]
        targets = [str(e.qa_id) for e in entries]
    applied = []
    for qa_id in targets:
        if await cognee.session.add_feedback(session_id=session_id, qa_id=qa_id,  # LIVE [TEST]
                                             feedback_text=text, feedback_score=score, **user_kw):
            applied.append(qa_id)
    await cognee.improve(dataset=dataset, session_ids=[session_id], **user_kw)  # LIVE [TEST]
    return {"feedback_applied": len(applied), "qa_ids": applied, "improved": True}


def apply_feedback(session_id: str, *, dataset: str, score: int | None = None, text: str | None = None,
                   qa_ids: Sequence[str] | None = None, user: Any | None = None) -> dict[str, Any]:
    """Synchronous wrapper of apply_feedback_async (do not call from inside a running event loop)."""
    return asyncio.run(apply_feedback_async(session_id, dataset=dataset, score=score, text=text, qa_ids=qa_ids,
                                            user=user))


def main(argv: list[str] | None = None) -> int:
    parser = ScriptParser(prog="python -m oracle.cognee_feedback",
                          description="Feed a scored run back into Cognee: session.add_feedback + improve().")
    parser.add_argument("--run-id", help="read run_seq, course and model_pts from the ledger `runs` row")
    parser.add_argument("--session-id", help="Cognee session (default: run-<run_seq> of --run-id)")
    parser.add_argument("--dataset", help="Cognee dataset (default: course-<slug> of the run's course)")
    parser.add_argument("--score", type=int, choices=range(1, 6), metavar="1..5", help="feedback score")
    parser.add_argument("--model-pts", type=float, help="derive the score from this points share (0..1)")
    parser.add_argument("--text", help="feedback text (default for a run: its points coverage)")
    parser.add_argument("--qa-id", action="append", default=[], help="rate only these Q&A entries (repeatable)")
    args = parser.parse_args(argv)
    if not args.run_id and not (args.session_id and args.dataset):
        parser.error("give --run-id, or both --session-id and --dataset")

    out: dict[str, Any] = {"ok": True}
    try:
        session_id, dataset, score, text = args.session_id, args.dataset, args.score, args.text
        pts = args.model_pts
        if args.run_id:
            session_id = session_id or session_id_for_run(args.run_id)
            out["run_id"] = args.run_id
            try:
                run = run_feedback_inputs(args.run_id)
            except LookupError as exc:
                if score is None and pts is None:
                    config.emit({**out, "session_id": session_id, "warning": f"{exc}; no feedback sent"})
                    return config.EXIT_OK
                run = None
            if run is not None:
                dataset = dataset or course_dataset_name(run["course"])
                pts = run["model_pts"] if pts is None else pts
        if dataset is None:
            parser.error("--dataset is required when the run is not in the ledger")
        if score is None and pts is None:
            parser.error("give --score, --model-pts, or a --run-id that has been scored")
        if score is None:
            score = score_from_points(pts)
        if text is None and args.run_id and pts is not None:
            text = f"Backtest {args.run_id}: the top-K covered {pts:.0%} of the exam's points."
        out.update(session_id=session_id, dataset=dataset, score=score, text=text)
        _check_inputs(session_id, dataset, score, text, args.qa_id)
    except (ValueError, config.ConfigError) as exc:
        config.emit({"ok": False, "error": str(exc)})
        return config.EXIT_HARD
    try:
        out.update(apply_feedback(session_id, dataset=dataset, score=score, text=text, qa_ids=args.qa_id or None))
    except Exception as exc:  # degraded on failure (rote-plays.md): never block the Play
        out["warning"] = f"cognee feedback failed: {exc}"
    config.emit(out)
    return config.EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
