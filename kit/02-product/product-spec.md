# Product Spec — Exam Oracle

## One line

A study planner that learns how professors write exams — and how each student learns — and gets better with every course it reads and every student it helps.

## The hook (say this first on stage)

> "Every student asks the same question: what's on the exam? This morning we hid one."

## Problem

Students have limited hours before an exam and no reliable way to decide where to spend them. Past exams exist, but reading them for patterns takes time most students don't have, and generic study advice ignores both how a particular professor writes exams and how a particular student learns.

## Users

- **Primary:** university students preparing for a specific exam in a specific course.
- **Secondary (pitch only):** tutoring companies; professors checking their own exams ("your exam tests topic X three times more than your lectures did").

## Core loop

1. Load a course: syllabus / lecture calendar, lecture notes, problem sets, past exams with solutions.
2. Tag every exam problem, homework problem and lecture against the course's fixed topic list.
3. Compute seven signals per topic for the target exam; combine them with the current **lessons** (learned weights) into a probability per topic.
4. Seal the prediction (SHA-256 + timestamp).
5. For backtests: score against a human-labeled answer key and two baselines; refit the lessons; store them. The next prediction uses the new lessons.
6. For a student: turn the prediction into a day-by-day plan and practice questions in that student's preferred style; learn from their feedback.

## Four levels of learning (the compounding story)

| Level | What improves | Evidence on screen |
|---|---|---|
| Per course | Each past exam sharpens that course's prediction | Points-covered rising across runs within a course |
| Across courses | Lessons about how exams are written carry to a course never seen | Course 3's cold-start prediction: lessons vs blank weights |
| Per professor's word | How much to trust what a syllabus says about exams | "The syllabus said X — past exams agreed 2 times in 3" |
| Per student | How this student likes to learn | Stored preference text before/after "too wordy" |

## Features

### MVP (must exist for the demo)

- Course loading via a Rote Play, replayed across courses (6.641 → course 2 → 6.003).
- Backtests via a Rote Play, each in its own throwaway hotdata database; lessons refit after each run.
- Sealed prediction for the 6.003 Fall 2011 Final with hash shown; reveal screen with hits/misses in points.
- Dashboard with the three charts (smarter / cheaper / more reliable) and both baselines.
- Student onboarding: preferences, exam date, weekly hours, syllabus + guideline upload.
- Study plan pipeline that uses the prediction and the student's preferences.
- Feedback that updates the student's preferences and changes the next answer.
- Snyk scans with history.

### Stretch

- Practice questions rewritten in the professor's style with new numbers.
- "Why" view as a Cypher path (open-source HydraDB) instead of a Cognee explanation.
- Publishing the `load-course` Play to the Rote Playoffs community catalog.
- Second demo student (Leo).
- Vector search for homework echoes (BM25 alone is acceptable).

## What it is not

- Not a cheating tool: it uses only material professors publish for students to study.
- Not a grade guarantee: it ranks where to spend hours, with stated uncertainty.
- Not a price-prediction or generic chatbot product.

## Why it can win

1. The hook lands in five seconds for anyone who was ever a student.
2. The sealed reveal is a fair test the room can check with its own eyes.
3. "Smarter" is measured against a human answer key and two baselines, not claimed.
4. All five sponsor tools do repeated work that visibly changes the result, and RocketRide is built with the sponsors' own nodes.
5. The student layer shows memory that is personal, isolated and adaptive — a second, human form of compounding.

## Success criteria

- M1 by 3:00: one sealed prediction scored end to end.
- M2 by 5:30: the 6.003 final sealed and its hash on screen.
- M3 by 7:15: backup video saved.
- Demo shows: ≥ 8 backtests on the chart with both baselines; the cold-start comparison; one student preference change; one Rote failure-and-resume; Snyk history.

## Pitch lines

- Close: "Every course it reads, and every student it helps, makes the next answer better."
- Compounding: "Muscle memory for loading courses, judgment memory for how exams are written, and personal memory for how you learn."
