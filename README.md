# Exam Oracle

A study planner that learns how a professor writes exams: it reads a course's MIT OpenCourseWare
exams, solutions, problem sets and lecture notes, predicts which topics will appear on an upcoming
exam, and turns that into a study plan shaped to each student. Built for the *Data and AI Hackathon:
From Memory to Muscle Memory* with Cognee, HydraDB, hotdata.dev, RocketRide.ai and Modiqo Rote.

The full plan is in [`kit/`](kit/START-HERE.md). Build rules: [`CLAUDE.md`](CLAUDE.md) (= `AGENTS.md`).

## Layout

| Path | What |
|---|---|
| `oracle/` | Python package behind the `exam-oracle` CLI — generic, input-driven loader scripts (role A) |
| `plays/load-course/` | The Rote Play that loads one course (index → download → extract → split → validate → hotdata → Cognee → notify) |
| `data/manifests/` | Committed manifests: SHA-256 → OCW URL, type, term, date for every downloaded file |
| `docs/` | Role A runbook, run evidence, Rote traces |
| `kit/` | The build kit (plan, tool dossiers, decisions log) |

## Load a course (role A)

```bash
brew install poppler hotdata-dev/tap/cli && hotdata auth login
uv tool install git+https://github.com/mrithikas11-tech/Exam-Oracle   # puts exam-oracle on PATH
export ORACLE_DATA_DIR=~/exam-oracle-data                              # outside the repo and the sealed folder
exam-oracle ledger-init                                                # once: hotdata ledger + keyed tables
cp -R plays/load-course ~/.rote/flows/
cd /tmp && rote play run load-course course=6.003 course_url=https://ocw.mit.edu/courses/6-003-signals-and-systems-fall-2011/
```

Details, results and handoff contracts for roles B and C: [`docs/person-a-loader.md`](docs/person-a-loader.md).

## Sealing

The 6.003 Fall 2011 Final and its solutions are on the skip list (`oracle/skip_list.txt`). The loader
never downloads, reads or loads them (a direct attempt exits 2), and they never enter the ledger.

## Credits

Course materials: **MIT OpenCourseWare**, https://ocw.mit.edu — licensed CC BY-NC-SA 4.0. Courses used:
6.641 Electromagnetic Fields, Forces, and Motion (Spring 2009); 6.003 Signals and Systems (Fall 2011);
2.71 Optics (Spring 2014). This project is non-commercial. Raw PDFs are not redistributed in this repository.

Security: Snyk scans are run by role C (`kit/05-build/security-checklist.md`); results will be recorded here.
