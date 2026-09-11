"""Tag pre-split exam problems onto a course's FIXED topic list with Cognee, and emit `exam_items` rows.

Spec: kit/04-tools/cognee.md "How Exam Oracle uses Cognee fully" (pre-split items; metadata comes from code and
the LLM only maps problem -> topics; remember(item, dataset_name="course-<code>", graph_model=ExamProblem,
node_set=[course, term, doctype], self_improvement=False); price first with dry_run=True);
kit/03-architecture/data-model.md (dataset course-<code> in its dataset-safe form, e.g. course-6-003; problem id
'<exam_id>-p<n>[<sub>]'); contracts/ledger-schema.sql `exam_items` (one row per (problem, topic);
points_share = points / number of topics tagged on the problem; tag_source 'cognee' | 'human'; NEVER holds a
sealed exam); contracts/README.md "Ground truth" (Cognee tags are model inputs; the AI never grades itself);
kit/BUILDER-RULES.md §4 (a sealed exam is never ingested) and §12 (self_improvement=False, dry_run first).

Items file: a JSON array (or {"items": [...]}) of pre-split problems,
    {"exam_id": "6.003-final-2009F", "problem": "4b", "points": 10, "text": "...", "text_clean": "... optional"}
exam_type, term, term_seq and session come from the ledger `exams` row (the loader's record, never the model);
every item's exam must be there and NOT sealed — checked before anything reaches Cognee.

Modes
  live         needs LLM_API_KEY. remember() each item into course-<code>, then read that dataset's graph
               (get_graph_engine().get_graph_data() under the dataset's database context) and derive
               problem -> topics, mapped onto the fixed list by TopicIndex. LIVE [TEST] throughout.
  --dry-run    live pricing: remember(..., dry_run=True) per item; no rows. Needs LLM_API_KEY too.
  --mock-tags  offline stand-in for Cognee: CSV exam_id, problem, topic_ids (';'-separated ids from the fixed
               list). Rows carry --tag-source (default 'cognee'; 'human' for hand tags).

Exit codes: 0 ok (untagged problems are a degraded warning); 1 hard fault (Cognee/service error, missing
ledger rows or topics, bad config); 2 an item belongs to a SEALED exam; 3 validation (bad items, tags outside
the fixed list); 7 LLM_API_KEY is not set for a live or dry run.
"""
from __future__ import annotations

import asyncio
import csv
import json
import math
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from oracle import config
from oracle._cli import ScriptParser, read_json_file
from oracle.backend import columns, get_backend, to_contract_table
from oracle.ontology import TopicIndex, TopicRow, build_ontology, course_slug, load_topics, serialize_ontology

EXIT_NO_LLM = 7
DOCTYPE = "exam"
TAG_SOURCES = ("cognee", "human")
MAX_TEXT_CHARS = 40_000
_PROBLEM_RE = re.compile(r"[0-9]{1,3}[a-z]{0,2}")
_LATEX_CMD_RE = re.compile(r"\\([A-Za-z]+)\*?")
_LATEX_PUNCT_RE = re.compile(r"[{}$^_\\&~%]")

EXAMS_SQL = ("SELECT exam_id, exam_type, term, term_seq, session, sealed FROM {{exams}} "
             "WHERE course = $course")


class TagError(Exception):
    """A refusal with its documented exit code."""

    def __init__(self, message: str, exit_code: int) -> None:
        super().__init__(message)
        self.exit_code = exit_code


@dataclass(frozen=True)
class Item:
    exam_id: str
    problem: str            # '4' or '4b' (contracts/README.md "Ids")
    points: float
    text: str
    text_clean: str | None = None

    @property
    def problem_id(self) -> str:
        return problem_id(self.exam_id, self.problem)


def course_dataset_name(course: str) -> str:
    """Cognee dataset of a course's shared knowledge: 'course-<slug>' (6.003 -> course-6-003)."""
    return f"course-{course_slug(course)}"


def problem_id(exam_id: str, problem: str) -> str:
    """ExamProblem identity: '<exam_id>-p<problem>' (data-model.md), unique across exams unlike `problem`."""
    return f"{exam_id}-p{problem}"


def clean_text(text: str) -> str:
    """LaTeX-stripped text for BM25 (ledger-schema.sql text_clean): '\\omega' -> 'omega', math punctuation -> space,
    whitespace collapsed, tokens over 40 bytes dropped (hotdata BM25 drops them anyway)."""
    stripped = _LATEX_PUNCT_RE.sub(" ", _LATEX_CMD_RE.sub(r" \1 ", text))
    return " ".join(tok for tok in stripped.split() if len(tok.encode("utf-8")) <= 40)


# ------------------------------------------------------------------ inputs
def parse_items(data: Any, course: str) -> list[Item]:
    """Validate the items file (see the module docstring). TagError(EXIT_VALIDATION) on any problem."""
    raw = data.get("items") if isinstance(data, Mapping) else data
    if not isinstance(raw, list) or not raw:
        raise TagError("items must be a non-empty JSON array (or {\"items\": [...]})", config.EXIT_VALIDATION)
    items: list[Item] = []
    seen: set[tuple[str, str]] = set()
    for n, obj in enumerate(raw, 1):
        where = f"item {n}"
        if not isinstance(obj, Mapping):
            raise TagError(f"{where}: must be an object", config.EXIT_VALIDATION)
        if obj.get("course", course) != course:
            raise TagError(f"{where}: course {obj.get('course')!r} != --course {course!r}", config.EXIT_VALIDATION)
        exam_id = obj.get("exam_id")
        if not config.is_valid_id(exam_id) or not str(exam_id).startswith(f"{course}-"):
            raise TagError(f"{where}: exam_id {exam_id!r} must be an id of the form {course}-<exam_type>-<term>",
                           config.EXIT_VALIDATION)
        problem = str(obj.get("problem", "")).strip().lower()
        if not _PROBLEM_RE.fullmatch(problem):
            raise TagError(f"{where}: problem {obj.get('problem')!r} must be a number with an optional sub-part letter",
                           config.EXIT_VALIDATION)
        points = obj.get("points")
        if isinstance(points, bool) or not isinstance(points, (int, float)) or not math.isfinite(points) or points <= 0:
            raise TagError(f"{where}: points must be a positive number (points_share = points / #topics)",
                           config.EXIT_VALIDATION)
        text, text_clean = obj.get("text"), obj.get("text_clean")
        if not isinstance(text, str) or not text.strip() or len(text) > MAX_TEXT_CHARS:
            raise TagError(f"{where}: text must be a non-empty string of at most {MAX_TEXT_CHARS} characters",
                           config.EXIT_VALIDATION)
        if text_clean is not None and not isinstance(text_clean, str):
            raise TagError(f"{where}: text_clean must be a string when given", config.EXIT_VALIDATION)
        if (exam_id, problem) in seen:
            raise TagError(f"{where}: duplicate problem {problem_id(exam_id, problem)}", config.EXIT_VALIDATION)
        seen.add((exam_id, problem))
        items.append(Item(exam_id, problem, float(points), text, text_clean))
    return items


def _opt_int(value: Any) -> int | None:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None  # pandas NA


def load_exams(course: str, backend: Any) -> dict[str, dict[str, Any]]:
    """exam_id -> {exam_type, term, term_seq, session, sealed} from the ledger `exams` table."""
    frame = backend.query(backend.ledger(), EXAMS_SQL, {"course": course})
    return {str(r["exam_id"]): {"exam_type": str(r["exam_type"]), "term": str(r["term"]),
                                "term_seq": int(r["term_seq"]), "session": _opt_int(r["session"]),
                                "sealed": bool(r["sealed"])}
            for r in frame.to_dict("records")}


def check_exams(course: str, items: Sequence[Item], exams: Mapping[str, Mapping[str, Any]]) -> None:
    """Refuse sealed exams (exit 2) and exams missing from the ledger (exit 1) BEFORE any Cognee call."""
    sealed = sorted({i.exam_id for i in items if exams.get(i.exam_id, {}).get("sealed")})
    if sealed:
        raise TagError(f"refusing to tag SEALED exam(s) {sealed}: sealed exams are never sent to Cognee or written "
                       f"to exam_items", config.EXIT_SKIP_LIST)
    unknown = sorted({i.exam_id for i in items if i.exam_id not in exams})
    if unknown:
        raise TagError(f"exam(s) {unknown} are not in the ledger `exams` table for {course}; load the exams first "
                       f"(their `sealed` flag is checked there)", config.EXIT_HARD)


def read_mock_tags(path: str | Path, index: TopicIndex, items: Sequence[Item]) -> tuple[dict[str, list[str]], list[str]]:
    """problem_id -> topic ids from a CSV (exam_id, problem, topic_ids). Topic ids must be on the fixed list
    (exit 3 otherwise). Rows for problems that are not among `items` are ignored and returned."""
    known = {i.problem_id for i in items}
    tags: dict[str, list[str]] = {}
    ignored: list[str] = []
    with open(path, newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        missing = {"exam_id", "problem", "topic_ids"} - set(reader.fieldnames or [])
        if missing:
            raise TagError(f"{path}: missing column(s) {sorted(missing)}", config.EXIT_VALIDATION)
        for line, row in enumerate(reader, 2):
            pid = problem_id((row["exam_id"] or "").strip(), (row["problem"] or "").strip().lower())
            ids = [t.strip() for t in (row["topic_ids"] or "").split(";") if t.strip()]
            bad = [t for t in ids if t not in index.ids()]
            if bad:
                raise TagError(f"{path}:{line}: topic id(s) {bad} are not on the fixed topic list", config.EXIT_VALIDATION)
            if pid not in known:
                ignored.append(pid)
                continue
            tags.setdefault(pid, []).extend(ids)
    return tags, ignored


# ------------------------------------------------------------------ graph -> tags
def topics_from_graph(nodes: Iterable[tuple[Any, Mapping[str, Any]]], edges: Iterable[Sequence[Any]],
                      wanted: Iterable[str], index: TopicIndex, *,
                      problem_node_ids: Mapping[str, Any] | None = None,
                      ) -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    """Derive problem_id -> topic ids from Cognee's graph, in the shape GraphDBInterface.get_graph_data() returns
    (nodes [(id, props)], edges [(source_id, target_id, relationship_name, props)]).

    A problem node has type 'ExamProblem' and a problem_id among `wanted` (case-insensitive), or its node id is
    given in `problem_node_ids` (ExamProblem.id_for(problem_id)). Its topics are the 'Topic' nodes on edges that
    touch it, mapped onto the FIXED list by TopicIndex (topic_id exactly, else the name, exact then fuzzy).
    Names that match nothing are dropped and returned as the second value — strict ontology behaviour, done
    here because cognee 1.5.4 skips ontology matching for custom graph models (see oracle/ontology.py)."""
    wanted_by_key = {pid.lower(): pid for pid in wanted}
    props_by_id = {str(nid): dict(props or {}) for nid, props in nodes}
    problem_of: dict[str, str] = {}
    for nid, props in props_by_id.items():
        if props.get("type") == "ExamProblem":
            pid = wanted_by_key.get(str(props.get("problem_id", "")).lower())
            if pid:
                problem_of[nid] = pid
    for pid, nid in (problem_node_ids or {}).items():
        if str(nid) in props_by_id and pid.lower() in wanted_by_key:
            problem_of[str(nid)] = wanted_by_key[pid.lower()]
    tags: dict[str, set[str]] = {}
    unmatched: dict[str, set[str]] = {}
    for edge in edges:
        source, target = str(edge[0]), str(edge[1])
        for a, b in ((source, target), (target, source)):
            pid, node = problem_of.get(a), props_by_id.get(b)
            if pid is None or node is None or node.get("type") != "Topic":
                continue
            topic_id = index.match_id(node.get("topic_id")) or index.match(node.get("name"))
            if topic_id:
                tags.setdefault(pid, set()).add(topic_id)
            else:
                unmatched.setdefault(pid, set()).add(str(node.get("name")))
    return ({pid: sorted(ids) for pid, ids in tags.items()},
            {pid: sorted(names) for pid, names in unmatched.items()})


# ------------------------------------------------------------------ rows
def exam_item_rows(course: str, items: Sequence[Item], exams: Mapping[str, Mapping[str, Any]],
                   tags: Mapping[str, Sequence[str]], tag_source: str) -> tuple[list[dict[str, Any]], list[str]]:
    """Contract `exam_items` rows (one per problem x topic, points split equally) and the untagged problem ids."""
    if tag_source not in TAG_SOURCES:
        raise ValueError(f"tag_source must be one of {TAG_SOURCES}")
    rows: list[dict[str, Any]] = []
    untagged: list[str] = []
    for item in items:
        topic_ids = sorted(set(tags.get(item.problem_id, ())))
        if not topic_ids:
            untagged.append(item.problem_id)
            continue
        exam = exams[item.exam_id]
        share = round(item.points / len(topic_ids), 6)
        text_clean = item.text_clean if item.text_clean is not None else clean_text(item.text)
        for topic_id in topic_ids:
            rows.append({"course": course, "exam_id": item.exam_id, "exam_type": exam["exam_type"],
                         "term": exam["term"], "term_seq": exam["term_seq"], "session": exam["session"],
                         "problem": item.problem, "points": item.points, "topic_id": topic_id,
                         "points_share": share, "tag_source": tag_source, "text_clean": text_clean})
    return rows, untagged


def write_rows_csv(path: str | Path, rows: Sequence[Mapping[str, Any]]) -> Path:
    """CSV with exactly the contract columns in contract order (NULL = empty cell, as read_contract_csv expects)."""
    names = [c.name for c in columns("exam_items")]
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(names)
        for row in rows:
            writer.writerow(["" if row[n] is None else row[n] for n in names])
    return out


# ------------------------------------------------------------------ live Cognee path
def require_llm_key() -> None:
    if not config.env("LLM_API_KEY"):
        raise TagError("LLM_API_KEY is not set: live Cognee tagging (and --dry-run pricing) needs it; "
                       "use --mock-tags <csv> to run offline", EXIT_NO_LLM)


def tagging_prompt(course: str, topics: Sequence[TopicRow]) -> str:
    """Extraction prompt: the LLM only maps the problem onto the fixed list (metadata is copied, not inferred)."""
    listing = "\n".join(f"{t.topic_id}: {t.topic}" for t in topics)
    return (f"You tag ONE exam problem from course {course} with topics from a FIXED list. Return an ExamProblem: "
            f"copy problem_id, exam_id and points exactly from the header lines; text = the problem text; "
            f"topics = every topic from the list below that the problem tests (usually one to three), each with "
            f"course=\"{course}\" and name and topic_id copied exactly from the list. Never invent a topic, and "
            f"leave topics empty if none fits.\nFixed topic list:\n{listing}")


def item_document(item: Item) -> str:
    return f"problem_id: {item.problem_id}\nexam_id: {item.exam_id}\npoints: {item.points:g}\n\n{item.text}"


async def _remember_items(course: str, items: Sequence[Item], exams: Mapping[str, Mapping[str, Any]],
                          topics: Sequence[TopicRow], *, dry_run: bool,
                          cognee_module: Any | None = None) -> list[Any]:
    """remember() each item into course-<slug> with graph_model=ExamProblem. LIVE [TEST].
    `cognee_module` replaces the imported cognee (tests pass a fake bound to the real signatures)."""
    from oracle.models import ExamProblem, import_cognee
    from oracle.ontology import cognee_ontology_config

    cognee = cognee_module if cognee_module is not None else import_cognee()
    dataset = course_dataset_name(course)
    ontology = cognee_ontology_config(serialize_ontology(build_ontology(topics, course)), mode="strict",
                                      name=f"{dataset}.ttl")
    prompt = tagging_prompt(course, topics)
    results = []
    for item in items:
        results.append(await cognee.remember(  # LIVE [TEST]
            item_document(item), dataset_name=dataset, graph_model=ExamProblem,
            node_set=[course, exams[item.exam_id]["term"], DOCTYPE], self_improvement=False,
            custom_prompt=prompt, config=ontology, dry_run=dry_run))
    return results


async def _read_course_graph(course: str) -> tuple[list[Any], list[Any]]:
    """All nodes and edges of the course dataset's graph (one graph database per dataset under access control).
    LIVE [TEST]: default-user ownership of the dataset is assumed (remember() above ran as the default user)."""
    from cognee.context_global_variables import set_database_global_context_variables
    from cognee.infrastructure.databases.graph import get_graph_engine
    from cognee.modules.data.methods import get_datasets_by_name
    from cognee.modules.users.methods import get_default_user

    user = await get_default_user()
    datasets = await get_datasets_by_name(course_dataset_name(course), user.id)
    if not datasets:
        raise RuntimeError(f"cognee dataset {course_dataset_name(course)} not found for the default user")
    dataset = datasets[0]
    async with set_database_global_context_variables(dataset.id, dataset.owner_id):
        engine = await get_graph_engine()
        nodes, edges = await engine.get_graph_data()
    return list(nodes), list(edges)


def tag_live(course: str, items: Sequence[Item], exams: Mapping[str, Mapping[str, Any]],
             topics: Sequence[TopicRow], index: TopicIndex) -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    """remember() + read the graph + derive tags. LIVE [TEST]."""
    from oracle.models import ExamProblem

    async def run() -> tuple[list[Any], list[Any]]:
        await _remember_items(course, items, exams, topics, dry_run=False)
        return await _read_course_graph(course)

    nodes, edges = asyncio.run(run())
    node_ids = {i.problem_id: str(ExamProblem.id_for(i.problem_id)) for i in items}
    return topics_from_graph(nodes, edges, [i.problem_id for i in items], index, problem_node_ids=node_ids)


# ------------------------------------------------------------------ CLI
def main(argv: list[str] | None = None) -> int:
    parser = ScriptParser(prog="python -m oracle.cognee_tag",
                          description="Tag pre-split exam problems onto the course's fixed topic list (Cognee) "
                                      "and write exam_items rows.")
    parser.add_argument("--course", required=True, help="course code, e.g. 6.003")
    parser.add_argument("--items", required=True, help="JSON file of pre-split problems")
    parser.add_argument("--topics", help="topics CSV with the contract columns (default: the ledger `topics` table)")
    parser.add_argument("--mock-tags", help="offline: CSV exam_id,problem,topic_ids instead of calling Cognee")
    parser.add_argument("--tag-source", choices=TAG_SOURCES, default="cognee",
                        help="tag_source of --mock-tags rows (live rows are always 'cognee')")
    parser.add_argument("--dry-run", action="store_true", help="price the live Cognee run; writes no rows")
    parser.add_argument("--out", help="write exam_items rows to this CSV (default: rows are included in the JSON)")
    args = parser.parse_args(argv)
    if args.dry_run and args.mock_tags:
        parser.error("--dry-run prices a live Cognee run and cannot be combined with --mock-tags")
    if args.tag_source != "cognee" and not args.mock_tags:
        parser.error("--tag-source applies to --mock-tags only; live tags are always 'cognee'")

    try:
        try:
            course = config.validate_id(args.course, what="course")
            data = read_json_file(args.items)
        except json.JSONDecodeError as exc:
            raise TagError(f"{args.items}: not valid JSON ({exc})", config.EXIT_VALIDATION) from exc
        items = parse_items(data, course)
        backend = get_backend()
        try:
            topics = load_topics(course, args.topics, backend=backend)
            index = TopicIndex(topics)
        except ValueError as exc:  # no or broken fixed topic list: a missing prerequisite, not bad items
            raise TagError(f"fixed topic list for {course}: {exc}", config.EXIT_HARD) from exc
        exams = load_exams(course, backend)
        check_exams(course, items, exams)
        summary: dict[str, Any] = {"course": course, "dataset": course_dataset_name(course), "items": len(items)}
        warnings: list[str] = []
        if args.mock_tags:
            tags, ignored = read_mock_tags(args.mock_tags, index, items)
            mode, tag_source, unmatched = "mock", args.tag_source, {}
            if ignored:
                warnings.append(f"{len(ignored)} mock tag row(s) name problems that are not among the items")
        else:
            require_llm_key()
            if args.dry_run:
                try:
                    estimates = asyncio.run(_remember_items(course, items, exams, topics, dry_run=True))
                except Exception as exc:  # service error: hard fault
                    raise TagError(f"cognee dry run failed: {exc}", config.EXIT_HARD) from exc
                config.emit({"ok": True, "mode": "dry-run", **summary, "estimates": [str(e) for e in estimates]})
                return config.EXIT_OK
            try:
                tags, unmatched = tag_live(course, items, exams, topics, index)
            except Exception as exc:  # service error: hard fault
                raise TagError(f"cognee tagging failed: {exc}", config.EXIT_HARD) from exc
            mode, tag_source = "live", "cognee"
        rows, untagged = exam_item_rows(course, items, exams, tags, tag_source)
        to_contract_table("exam_items", rows)  # rows must fit the contract before anyone loads them
        if untagged:
            warnings.append(f"{len(untagged)} problem(s) got no topic from the fixed list")
        if unmatched:
            warnings.append(f"topic names outside the fixed list were dropped for {len(unmatched)} problem(s)")
        out: dict[str, Any] = {"ok": True, "mode": mode, **summary, "tagged": len(items) - len(untagged),
                               "row_count": len(rows), "tag_source": tag_source, "untagged": untagged,
                               "unmatched_names": unmatched}
        if args.out:
            out["out"] = str(write_rows_csv(args.out, rows))
        else:
            out["rows"] = rows
        if warnings:
            out["warning"] = "; ".join(warnings)
        config.emit(out)
        return config.EXIT_OK
    except TagError as exc:
        config.emit({"ok": False, "error": str(exc)})
        return exc.exit_code
    except config.ConfigError as exc:
        config.emit({"ok": False, "error": str(exc)})
        return config.EXIT_HARD
    except ValueError as exc:
        config.emit({"ok": False, "error": str(exc)})
        return config.EXIT_VALIDATION
    except OSError as exc:
        config.emit({"ok": False, "error": str(exc)})
        return config.EXIT_HARD


if __name__ == "__main__":
    sys.exit(main())
