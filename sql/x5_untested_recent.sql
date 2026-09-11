-- x5  Untested recent material.  Spec: kit/02-product/prediction-model.md, signal row x5.
-- Finals only; full feature set only.
--
--   quizzes   = visible exams of T's term other than finals (quiz1 | quiz2 | quiz3 | midterm) with a known session
--   last_quiz = max(quizzes.session)
--   x5(t) = 1  if T.exam_type = 'final'
--              AND t has a visible lecture in T's term with session > last_quiz
--              AND t is not tagged on any quiz of T's term
--         = 0  otherwise.
--   A term with no visible quiz has no "last quiz": no rows, x5 = 0 for every topic (a flag that would be 1
--   for every taught topic carries no ranking information).
-- Params: course, exam_type, target_exam, target_term_seq, target_session.  Returns (topic_id, x5).
WITH quizzes AS (
    SELECT e.exam_id, e."session"
    FROM {{exams}} AS e
    WHERE e.course = $course
      AND e.term_seq = $target_term_seq
      AND e.exam_type <> 'final'
      AND e.exam_id <> $target_exam
      AND e."session" IS NOT NULL
      AND @VISIBLE(e.term_seq, e.session)
),
tested AS (
    SELECT DISTINCT i.topic_id
    FROM {{exam_items}} AS i
    JOIN quizzes AS q ON q.exam_id = i.exam_id
    WHERE @VISIBLE(i.term_seq, i.session)
),
recent AS (
    SELECT DISTINCT l.topic_id
    FROM {{lectures}} AS l
    WHERE l.course = $course
      AND l.term_seq = $target_term_seq
      AND l.topic_id IS NOT NULL
      AND @VISIBLE(l.term_seq, l.session)
      AND l."session" > (SELECT max(q2."session") FROM quizzes AS q2)
)
SELECT r.topic_id, CAST(1.0 AS DOUBLE) AS x5
FROM recent AS r
WHERE $exam_type = 'final'
  AND r.topic_id NOT IN (SELECT t.topic_id FROM tested AS t)
ORDER BY r.topic_id
