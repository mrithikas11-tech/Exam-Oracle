# Demo Script (3 minutes)

Rehearse twice. Keep the backup video open in a second window, offline.

## Stage setup (before you walk up)

- Tabs, in order: Dashboard (hash card visible) · terminal with `rote trace --html` output and last `rote stats` · Reveal screen (not yet run) · Maya's plan · Leo's plan · "If removed" slide · credits slide.
- The prediction file and its printed hash + timestamp from hours earlier.
- Phone/laptop hotspot as Wi-Fi fallback.

## Script

| Time | Say / do |
|---|---|
| 0:00 | "Every student asks the same question: what's on the exam? This morning we hid one." Point to the sealed card: hash and the time it was made. |
| 0:15 | **Cheaper.** "Course 1 took the agent N minutes and M tokens to learn how to read. Courses 2 and 3 were replayed by Rote in seconds with near-zero model tokens." Flash the trace Gantt. (Use real numbers from the ledger.) |
| 0:35 | **Smarter.** The chart of points covered per run with both baselines. Then the cold-start comparison: "6.003 with lessons from the other courses vs blank weights." |
| 1:00 | **Reveal.** Recompute the hash live — it matches. Open the real 6.003 Fall 2011 Final. Problems light up as hits or misses with their points. State the score and both baselines honestly. |
| 1:35 | **Why.** Click one hit: "It echoes Homework N, problem M; the homework-echo lesson was learned on 6.641." Then: "The syllabus said the final emphasizes the last third — past exams agreed X times in Y." |
| 1:55 | **Students.** Maya and Leo: same prediction, two plans (order, length, schedule). Maya taps "too wordy"; her next explanation is shorter; show her stored preference changing in HydraDB. (Fictional students, labeled.) |
| 2:25 | **Reliable.** First-try check rate and calibration. The Rote moment: a scanned PDF failed its check, the step was BLOCKED with evidence, fixed, resumed. |
| 2:45 | "If removed" slide (five tools, one line each). Close: "Every course it reads, and every student it helps, makes the next answer better. Credit to MIT OpenCourseWare." |

## If something breaks on stage

- Reveal pipeline fails → run the laptop `score.py` + show the static reveal; keep the hash recompute.
- App down → switch to the backup video at the current timestamp; keep talking.
- A number looks bad → say it; point to the baseline comparison and n.
