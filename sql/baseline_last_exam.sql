-- Baseline B, "copy last exam".  Spec: kit/02-product/prediction-model.md "Scoring": topics of the most
-- recent exam of the same type, ranked by their points there; pad with lecture time to K. Both feature sets.
--
--   last exam = the visible, non-sealed, tagged exam of this course with T's exam_type and the greatest
--               (term_seq, session); an unknown session sorts as end of term (1000000 = ordering.END_OF_TERM)
--   points(t) = sum of points_share of its items on t;  order = points DESC, topic_id ASC.
--   Padding to K with baseline A's order is done in oracle/run_signals.py (it needs x4); rank_and_seal
--   keeps the first K.
-- Params: course, exam_type, target_exam, target_term_seq, target_session.
-- Returns (exam_id, topic_id, points), ordered.
WITH prior AS (
    SELECT e.exam_id, e.term_seq, COALESCE(e."session", 1000000) AS s
    FROM {{exams}} AS e
    WHERE e.course = $course
      AND e.exam_type = $exam_type
      AND e.exam_id <> $target_exam
      AND NOT e.sealed
      AND @VISIBLE(e.term_seq, e.session)
      AND e.exam_id IN (SELECT i.exam_id FROM {{exam_items}} AS i WHERE @VISIBLE(i.term_seq, i.session))
),
last_exam AS (
    SELECT p.exam_id FROM prior AS p ORDER BY p.term_seq DESC, p.s DESC, p.exam_id DESC LIMIT 1
)
SELECT i.exam_id, i.topic_id, sum(i.points_share) AS points
FROM {{exam_items}} AS i
JOIN last_exam AS le ON le.exam_id = i.exam_id
WHERE @VISIBLE(i.term_seq, i.session)
GROUP BY i.exam_id, i.topic_id
ORDER BY points DESC, i.topic_id ASC
