-- x6  Already tested.  Spec: kit/02-product/prediction-model.md, signal row x6 ("share of this term's quiz
-- points on t; weight learned; may be positive or negative"). Full feature set only.
--
--   quiz items = visible exam_items of the visible, non-sealed, non-final exams of T's term (its quizzes and
--                midterm so far; never T itself)
--   x6(t) = sum(points_share of quiz items on t) / sum(points_share of all quiz items)
--
--   points_share splits a problem's points equally over its tagged topics (ledger-schema.sql exam_items), so
--   the denominator is the term's tagged quiz points. No quiz points yet -> NULL/no rows -> 0.
-- Params: course, target_exam, target_term_seq, target_session.  Returns (topic_id, x6).
WITH quiz_items AS (
    SELECT i.topic_id, i.points_share
    FROM {{exam_items}} AS i
    JOIN {{exams}} AS e ON e.exam_id = i.exam_id
    WHERE e.course = $course
      AND e.term_seq = $target_term_seq
      AND e.exam_type <> 'final'
      AND e.exam_id <> $target_exam
      AND NOT e.sealed
      AND @VISIBLE(e.term_seq, e.session)
      AND @VISIBLE(i.term_seq, i.session)
),
total AS (
    SELECT sum(q.points_share) AS pts FROM quiz_items AS q
)
SELECT qi.topic_id,
       sum(qi.points_share) / (SELECT nullif(t.pts, 0) FROM total AS t) AS x6
FROM quiz_items AS qi
GROUP BY qi.topic_id
ORDER BY qi.topic_id
