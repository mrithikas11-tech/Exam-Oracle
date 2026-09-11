# Exam Oracle

A study planner that learns how a professor writes exams: it reads a course's MIT OpenCourseWare
exams, solutions, problem sets and lecture notes, predicts which topics will appear on an upcoming
exam, and turns that into a study plan shaped to each student. Built for the *Data and AI Hackathon:
From Memory to Muscle Memory* with Cognee, HydraDB, hotdata.dev, RocketRide.ai and Modiqo Rote.

The full plan is in [`kit/`](kit/START-HERE.md). Build rules: [`CLAUDE.md`](CLAUDE.md) (= `AGENTS.md`).

## Layout

| Path | What |
|---|---|
| `oracle/` | One package: the loader behind the `exam-oracle` CLI (role A) and the model & memory scripts (role B) |
| `contracts/` | The ledger schema, prediction schema and fixtures every role builds against (owner: B) |
| `data/courses/` | Course structure per course: topics, sessions, exams, homework sets, guidance (owner: B; 2.71 drafted by A) |
| `plays/load-course/` | The Rote Play that loads one course (index → download → extract → split → validate → structure → Cognee tag → homework → summary → notify) |
| `data/manifests/` | Committed manifests: SHA-256 → OCW URL, type, term, date for every downloaded file |
| `docs/` | Role A runbook, run evidence, Rote traces |
| `kit/` | The build kit (plan, tool dossiers, decisions log) |

## Load a course (role A)

```bash
brew install poppler
uv tool install --force --editable .                  # from a checkout: exam-oracle + B's modules
export ORACLE_DATA_DIR=~/exam-oracle-data ORACLE_LOCAL_DIR=~/exam-oracle-data/local-backend
exam-oracle ledger-init
cp -R plays/load-course ~/.rote/flows/
cd /tmp && rote play run load-course course=6.003 course_url=https://ocw.mit.edu/courses/6-003-signals-and-systems-fall-2011/
```

Tests (B's suite, offline): `python -m pytest -q` with `pip install -r requirements-b.txt`.

Details, results and handoff contracts for roles B and C: [`docs/person-a-loader.md`](docs/person-a-loader.md).

## Sealing

The 6.003 Fall 2011 Final and its solutions are on the skip list (`oracle/skip_list.txt`). The loader
never downloads, reads or loads them (a direct attempt exits 2), and they never enter the ledger.

## Credits

Course materials: **MIT OpenCourseWare**, https://ocw.mit.edu — licensed CC BY-NC-SA 4.0. Courses used:
6.641 Electromagnetic Fields, Forces, and Motion (Spring 2009); 6.003 Signals and Systems (Fall 2011);
2.71 Optics (Spring 2014); 18.06 Linear Algebra (Spring 2010) structure files. This project is non-commercial. Raw PDFs are not redistributed in this repository.

Security: Snyk scans are run by role C (`kit/05-build/security-checklist.md`); results will be recorded here.
