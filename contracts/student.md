# Student contract (B ↔ C)

Owner: Person B (stores) with Person C (app). Background: `kit/02-product/student-layer.md`.

## 1. StudentProfile

| Field | Type | Allowed values | Example (Maya, fictional) |
|---|---|---|---|
| `student_id` | string | `s<n>` | `s1` |
| `display_name` | string | shown in the app, labeled "fictional" in the demo | `Maya (fictional)` |
| `course` | string | a loaded course code | `6.003` |
| `exam_id` | string | an `exams.exam_id` | `6.003-final-2011F` |
| `exam_date` | string | ISO date | `2026-09-20` |
| `weekly_hours` | number | 0.5–60 | `6` |
| `style_order` | string | `examples_first` \| `proofs_first` | `examples_first` |
| `length` | string | `short` \| `detailed` | `short` |
| `modality` | string | `visual` \| `verbal` | `visual` |
| `pace` | string | `slow` \| `normal` \| `fast` | `normal` |

Leo (fictional): `s2`, `proofs_first`, `detailed`, `verbal`, `normal`, 12 h/week, exam in 16 days.

## 2. Where it is stored

| Store | Location | Keys / names |
|---|---|---|
| HydraDB (hosted) | database `exam-oracle`, collection `s<n>` | explicit preferences, stored verbatim (`infer:false`), fixed ids so updates replace: `s<n>_pref_style`, `s<n>_pref_length`, `s<n>_pref_modality`, `s<n>_pref_pace`; value text e.g. `"style_order=examples_first"` |
| HydraDB (hosted) | same collection | feedback memories, `infer:true`, id `s<n>_fb_<seq>`; HydraDB extracts the lasting preference |
| Cognee | dataset `student-<n>` (private; only that user) | `StudentProfile` DataPoint (all fields above), the student's `Syllabus`, sessions |
| hotdata `ledger` | table `students` | `student_id, course, exam_id, exam_date, weekly_hours` (planner arithmetic) |

Shared course knowledge: Cognee dataset `course-<code>` (read-only to students); HydraDB collection `shared` (lessons, predictions). A student's requests may only read `course-<code>`, `student-<their n>`, `shared`, and collection `s<their n>`.

HydraDB memory item shapes (verified against HydraDB docs `llms.txt` §6, 2026-09-11), sent as a JSON-array string in `client.context.ingest(database="exam-oracle", collection="s1", type="memory", memories=..., upsert="true")`:

```json
[{"id": "s1_pref_style", "title": "Teaching order", "text": "style_order=examples_first", "infer": false,
  "additional_metadata": {"kind": "explicit_pref", "field": "style_order"}}]
```
```json
[{"id": "s1_fb_0003", "title": "Feedback on plan answer", "text": "too wordy", "infer": true,
  "custom_instructions": "Extract a durable teaching preference about explanation length, order, modality or pace.",
  "additional_metadata": {"kind": "feedback", "session_id": "s1-plan-0003", "score": 2}}]
```

A HydraDB query searches **one** collection, so reading "this student + shared lessons" is two `client.query(...)` calls.

## 3. Feedback event (C → B)

```json
{
  "student_id": "s1",
  "session_id": "s1-plan-0003",
  "qa_id": "optional id of the answer being rated",
  "text": "too wordy",
  "score": 2,
  "created_at": "2026-09-11T15:02:11-07:00"
}
```

`score` is 1–5 (optional; `null` if only text). B's handler: HydraDB `infer:true` memory in `s<n>`; Cognee `add_feedback(session_id, qa_id, feedback_text=text, feedback_score=score)` then `improve(session_ids=[session_id])`.

## 4. Preference read (B → C)

```json
{
  "student_id": "s1",
  "explicit": {"style_order": "examples_first", "length": "short", "modality": "visual", "pace": "normal"},
  "inferred": ["Prefers concise explanations (from feedback 'too wordy')"],
  "system_prompt": "Teach with a worked example before any theory. Keep each explanation under 120 words. Prefer diagrams described in words. Normal pace."
}
```

`system_prompt` is what C's study-plan and practice pipelines pass to the LLM. B builds it from `explicit` + `inferred`.

## 5. Study plan (C renders; B's prediction feeds it)

```json
{
  "student_id": "s1",
  "exam_id": "6.003-final-2011F",
  "prediction_hash": "<sha256 of the sealed prediction>",
  "days": [
    {"date": "2026-09-12", "minutes": 50, "topics": ["T05"], "activities": ["worked example", "2 practice problems"]}
  ],
  "style": {"style_order": "examples_first", "length": "short"},
  "word_count": 412
}
```

`word_count` is logged so the demo can show the "too wordy" effect as a number.

## 6. Privacy rules

- Store only what the student gives. Demo students are fictional and labeled.
- Never put another student's memory into a prompt.
- "Delete my data" = Cognee `forget(everything=True, user=<student>)` — only after the smoke test proves other students' sessions survive.
