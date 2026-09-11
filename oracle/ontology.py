"""OWL ontology of a course's FIXED topic list, for Cognee's ontology resolver in strict mode.

Spec: kit/04-tools/cognee.md "How Exam Oracle uses Cognee fully" step 1 (build the topic list first; create an
OWL file with rdflib, pass it as a file object with ontology_mode="strict"); kit/02-product/prediction-model.md
(a FIXED list of 12–20 topics per course; the tagger must not invent topics).

Verified against the installed cognee 1.5.4 source (cognee/modules/ontology/):
  * Per-call config: {"ontology_config": {"ontology_resolver": <BaseOntologyResolver>, "ontology_mode": "strict"}}
    (ontology_config.py Config / OntologyConfig, read by get_configured_ontology_resolver / _mode in
    get_default_ontology_resolver.py). remember() routes the `config` keyword to cognify (remember.py _COGNIFY_ONLY).
  * Environment equivalent: ONTOLOGY_FILE_PATH, ONTOLOGY_MODE, ONTOLOGY_RESOLVER=rdflib, MATCHING_STRATEGY=fuzzy
    (ontology_env_config.py).
  * RDFLibOntologyResolver(ontology_file=<path | file object | list>, matching_strategy=FuzzyMatchingStrategy())
    keys every owl:Class ("classes") and every subject typed with one of those classes ("individuals") by its IRI
    fragment, lower-cased with spaces -> "_". An extracted node's type is matched against the classes and its
    name against the individuals (difflib, cutoff 0.8). Strict mode refuses an empty ontology up front.
  * LIMIT in 1.5.4: tasks/graph/extract_graph_from_data.py integrate_chunk_graphs() applies the ontology only when
    graph_model is a KnowledgeGraph subclass. With a custom DataPoint graph_model (our ExamProblem) it returns
    before any ontology matching, so strict mode does NOT filter those nodes. cognee_tag therefore enforces the
    fixed list itself with TopicIndex (same normalisation idea, same 0.8 cutoff) and still passes this config.

The serialisation is Turtle: rdflib sorts subjects and predicates when writing it, so the same topic list always
gives the same bytes (Rote replays must not produce different results for the same call).
"""
from __future__ import annotations

import difflib
import io
import math
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

from rdflib import Graph, Literal, Namespace, URIRef
from rdflib.namespace import OWL, RDF, RDFS, XSD

from oracle import config
from oracle._cli import ScriptParser

BASE_IRI = "https://exam-oracle.invalid/ontology/"   # .invalid is reserved (RFC 2606): never dereferenced
CLASS_NAMES = ("Topic", "ExamProblem", "HomeworkProblem", "Lecture", "ExamWindow", "StatedGuideline", "Syllabus")
FUZZY_CUTOFF = 0.8                                    # cognee FuzzyMatchingStrategy default
ONTOLOGY_MODES = ("strict", "annotate")
ONTOLOGY_FORMAT = "turtle"
ONTOLOGY_SUFFIX = ".ttl"

TOPICS_SQL = ("SELECT course, topic_id, topic, first_lecture, last_lecture FROM {{topics}} "
              "WHERE course = $course ORDER BY topic_id")


@dataclass(frozen=True)
class TopicRow:
    """One row of the contract `topics` table."""

    course: str
    topic_id: str
    topic: str
    first_lecture: int | None = None
    last_lecture: int | None = None


def course_slug(course: str) -> str:
    """Dataset- and IRI-safe course code (data-model.md: '6.003' -> '6-003'; cognee rejects '.' in dataset names)."""
    return re.sub(r"[^A-Za-z0-9_-]", "-", config.validate_id(course, what="course"))


def name_key(name: str) -> str:
    """Matching key of a topic name: lower-case, apostrophes dropped, each run of other non-alphanumerics -> '_'
    ('Z-transform', 'z transform' and 'Z_Transform' share the key 'z_transform')."""
    return re.sub(r"[^a-z0-9]+", "_", name.lower().replace("'", "")).strip("_")


def iri_fragment(name: str) -> str:
    """IRI fragment of a topic individual; the resolver's key for it (fragment.lower()) equals name_key(name)."""
    return re.sub(r"[^A-Za-z0-9]+", "_", name.replace("'", "")).strip("_")


def _opt_int(value: Any) -> int | None:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None  # pandas NA and friends


def check_topics(topics: Sequence[TopicRow]) -> list[TopicRow]:
    """The list must be non-empty, one course, unique topic ids, and names that stay distinct after name_key()."""
    rows = list(topics)
    if not rows:
        raise ValueError("the topic list is empty; build the course's fixed topic list first")
    courses = {t.course for t in rows}
    if len(courses) != 1:
        raise ValueError(f"a topic list must belong to one course, got {sorted(courses)}")
    ids: set[str] = set()
    keys: dict[str, str] = {}
    for t in rows:
        config.validate_id(t.topic_id, what="topic_id")
        key = name_key(t.topic)
        if not key:
            raise ValueError(f"topic {t.topic_id} has no usable name: {t.topic!r}")
        if t.topic_id in ids:
            raise ValueError(f"duplicate topic_id {t.topic_id}")
        if key in keys:
            raise ValueError(f"topics {keys[key]} and {t.topic_id} have the same name after normalisation ({key!r})")
        ids.add(t.topic_id)
        keys[key] = t.topic_id
    return rows


def _rows_to_topics(records: list[dict[str, Any]], course: str) -> list[TopicRow]:
    return [TopicRow(course=str(r["course"]), topic_id=str(r["topic_id"]), topic=str(r["topic"]),
                     first_lecture=_opt_int(r.get("first_lecture")), last_lecture=_opt_int(r.get("last_lecture")))
            for r in records if str(r["course"]) == course]


def read_topics_csv(path: str | Path, course: str) -> list[TopicRow]:
    """Topics of `course` from a CSV with exactly the contract `topics` columns (e.g. contracts/fixtures/topics.csv)."""
    from oracle.backend import read_contract_csv

    course = config.validate_id(course, what="course")
    return check_topics(_rows_to_topics(read_contract_csv(path, "topics").to_dict("records"), course))


def read_topics_ledger(course: str, backend: Any | None = None) -> list[TopicRow]:
    """Topics of `course` from the ledger `topics` table (parameterised query)."""
    from oracle.backend import get_backend

    course = config.validate_id(course, what="course")
    be = backend if backend is not None else get_backend()
    frame = be.query(be.ledger(), TOPICS_SQL, {"course": course})
    return check_topics(_rows_to_topics(frame.to_dict("records"), course))


def load_topics(course: str, topics_csv: str | Path | None = None, backend: Any | None = None) -> list[TopicRow]:
    """The fixed topic list: from `topics_csv` when given, else from the ledger."""
    return read_topics_csv(topics_csv, course) if topics_csv else read_topics_ledger(course, backend)


class TopicIndex:
    """The fixed topic list as a matcher: what strict ontology mode is meant to do, applied in our own code."""

    def __init__(self, topics: Sequence[TopicRow]) -> None:
        self.topics = check_topics(topics)
        self._by_id = {t.topic_id.upper(): t.topic_id for t in self.topics}
        self._by_key = {name_key(t.topic): t.topic_id for t in self.topics}
        self._names = {t.topic_id: t.topic for t in self.topics}

    def ids(self) -> set[str]:
        return set(self._names)

    def name(self, topic_id: str) -> str:
        return self._names[topic_id]

    def match_id(self, value: Any) -> str | None:
        """Exact topic id (case-insensitive), else None."""
        return self._by_id.get(value.strip().upper()) if isinstance(value, str) else None

    def match(self, value: Any) -> str | None:
        """Topic id for a topic id or name: exact id, exact normalised name, then fuzzy (difflib, cutoff 0.8).
        None if nothing on the fixed list is close enough (strict: the tagger may not invent topics)."""
        if not isinstance(value, str) or not value.strip():
            return None
        found = self.match_id(value)
        if found:
            return found
        key = name_key(value)
        if key in self._by_key:
            return self._by_key[key]
        close = difflib.get_close_matches(key, list(self._by_key), n=1, cutoff=FUZZY_CUTOFF)
        return self._by_key[close[0]] if close else None


def build_ontology(topics: Sequence[TopicRow], course: str) -> Graph:
    """OWL ontology: one owl:Class per Cognee model name, and one individual of class Topic per fixed topic
    (rdfs:label = the topic name, datatype properties topicId and course). Classes, topics and properties live
    in separate namespaces so a topic named like a class cannot collide with it."""
    course = config.validate_id(course, what="course")
    rows = check_topics(topics)
    if rows[0].course != course:
        raise ValueError(f"topic list is for {rows[0].course!r}, not {course!r}")
    base = f"{BASE_IRI}{course_slug(course)}"
    cls_ns, topic_ns, prop_ns = Namespace(f"{base}/class#"), Namespace(f"{base}/topic#"), Namespace(f"{base}/property#")
    graph = Graph()
    for prefix, ns in (("eoc", cls_ns), ("eot", topic_ns), ("eop", prop_ns), ("owl", OWL)):
        graph.bind(prefix, ns)
    ontology = URIRef(base)
    graph.add((ontology, RDF.type, OWL.Ontology))
    graph.add((ontology, RDFS.label, Literal(f"Exam Oracle fixed topic list for {course}")))
    for name in CLASS_NAMES:
        graph.add((cls_ns[name], RDF.type, OWL.Class))
        graph.add((cls_ns[name], RDFS.label, Literal(name)))
    for prop in ("topicId", "course"):
        graph.add((prop_ns[prop], RDF.type, OWL.DatatypeProperty))
        graph.add((prop_ns[prop], RDFS.domain, cls_ns["Topic"]))
        graph.add((prop_ns[prop], RDFS.range, XSD.string))
    for t in rows:
        node = topic_ns[iri_fragment(t.topic)]
        graph.add((node, RDF.type, OWL.NamedIndividual))
        graph.add((node, RDF.type, cls_ns["Topic"]))
        graph.add((node, RDFS.label, Literal(t.topic)))
        graph.add((node, prop_ns["topicId"], Literal(t.topic_id)))
        graph.add((node, prop_ns["course"], Literal(course)))
    return graph


def serialize_ontology(graph: Graph) -> bytes:
    """Turtle bytes (deterministic for the same triples)."""
    return graph.serialize(format=ONTOLOGY_FORMAT).encode("utf-8")


def write_ontology(topics: Sequence[TopicRow], course: str, path: str | Path) -> Path:
    """Write the course ontology to `path` (created folders as needed) and return it."""
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(serialize_ontology(build_ontology(topics, course)))
    return out


def ontology_env(path: str | Path, mode: str = "strict") -> dict[str, str]:
    """Environment variables that select the same ontology globally (cognee OntologyEnvConfig)."""
    if mode not in ONTOLOGY_MODES:
        raise ValueError(f"ontology mode must be one of {ONTOLOGY_MODES}")
    return {"ONTOLOGY_FILE_PATH": str(path), "ONTOLOGY_MODE": mode, "ONTOLOGY_RESOLVER": "rdflib",
            "MATCHING_STRATEGY": "fuzzy"}


def cognee_ontology_config(ontology: bytes | str | Path, *, mode: str = "strict",
                           name: str = f"topics{ONTOLOGY_SUFFIX}") -> dict[str, Any]:
    """The per-call config for cognee.cognify(config=...) / cognee.remember(config=...):
    {"ontology_config": {"ontology_resolver": RDFLibOntologyResolver, "ontology_mode": mode}}.

    `ontology` is Turtle bytes (passed as an in-memory file object named `name`, so rdflib guesses the format)
    or a path. In strict mode an ontology without entries is refused up front, as cognee does for
    ONTOLOGY_FILE_PATH. Imports cognee (through models.import_cognee)."""
    if mode not in ONTOLOGY_MODES:
        raise ValueError(f"ontology mode must be one of {ONTOLOGY_MODES}")
    from oracle.models import import_cognee

    import_cognee()
    from cognee.modules.ontology.construct_data_points_and_edges_with_ontology import (
        ensure_ontology_usable_in_strict_mode)
    from cognee.modules.ontology.matching_strategies import FuzzyMatchingStrategy
    from cognee.modules.ontology.rdf_xml.RDFLibOntologyResolver import RDFLibOntologyResolver

    if isinstance(ontology, bytes):
        source: Any = io.BytesIO(ontology)
        source.name = name
    else:
        path = Path(ontology)
        if not path.is_file():
            raise FileNotFoundError(f"ontology file not found: {path}")
        source = str(path)
    resolver = RDFLibOntologyResolver(ontology_file=source, matching_strategy=FuzzyMatchingStrategy(cutoff=FUZZY_CUTOFF))
    if mode == "strict":
        ensure_ontology_usable_in_strict_mode(resolver)
    return {"ontology_config": {"ontology_resolver": resolver, "ontology_mode": mode}}


def main(argv: list[str] | None = None) -> int:
    parser = ScriptParser(prog="python -m oracle.ontology",
                          description="Build the OWL (Turtle) ontology of a course's fixed topic list for Cognee strict mode.")
    parser.add_argument("--course", required=True, help="course code, e.g. 6.003")
    parser.add_argument("--topics", help="topics CSV with the contract columns (default: the ledger `topics` table)")
    parser.add_argument("--out", help=f"output file (default: data/topics/<course-slug>{ONTOLOGY_SUFFIX})")
    parser.add_argument("--mode", choices=ONTOLOGY_MODES, default="strict")
    args = parser.parse_args(argv)
    try:
        course = config.validate_id(args.course, what="course")
        topics = load_topics(course, args.topics)
        out = Path(args.out) if args.out else config.TOPICS_DIR / f"{course_slug(course)}{ONTOLOGY_SUFFIX}"
        path = write_ontology(topics, course, out)
        data = path.read_bytes()
    except Exception as exc:  # validation, file or ledger error: still one JSON object, exit 1
        config.emit({"ok": False, "error": str(exc) if isinstance(exc, (ValueError, OSError))
                     else f"{type(exc).__name__}: {exc}"})
        return config.EXIT_HARD
    config.emit({"ok": True, "course": course, "path": str(path), "format": ONTOLOGY_FORMAT, "topics": len(topics),
                 "classes": list(CLASS_NAMES), "sha256": config.sha256_hex(data), "mode": args.mode,
                 "env": ontology_env(path, args.mode)})
    return config.EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
