-- x1  Track record.  Spec: kit/02-product/prediction-model.md, signal row x1. Both feature sets.
--
--   x1(t) = sum_e w(e) * 1[t is tagged on e]  /  sum_e w(e)
--
--   e ranges over the PRIOR exams: same course and same exam_type as the target T, visible to T under the
--   time-ordering rule (contracts/README.md), not sealed, not T itself, with at least one visible tagged item
--   (an untagged exam says nothing about topics, so it is not allowed to dilute the denominator).
--   w(e) = 0.7 ^ d(e), where d(e) = the number of DISTINCT exam terms of that prior set that lie strictly
--   between e's term and T's term (term_seq order). The most recent prior term has d = 0 and weight 1.
--   No prior exam -> no rows -> x1 = 0 for every topic (oracle/run_signals.py fills absent topics with 0).
-- Params: course, exam_type, target_exam, target_term_seq, target_session.  Returns (topic_id, x1).
WITH prior AS (
    SELECT e.exam_id, e.term_seq
    FROM {{exams}} AS e
    WHERE e.course = $course
      AND e.exam_type = $exam_type
      AND e.exam_id <> $target_exam
      AND NOT e.sealed
      AND @VISIBLE(e.term_seq, e.session)
      AND e.exam_id IN (SELECT i.exam_id FROM {{exam_items}} AS i WHERE @VISIBLE(i.term_seq, i.session))
),
prior_terms AS (
    SELECT DISTINCT term_seq FROM prior
),
weighted AS (
    SELECT p.exam_id, power(0.7, count(pt.term_seq)) AS weight
    FROM prior AS p
    LEFT JOIN prior_terms AS pt
      ON pt.term_seq > p.term_seq AND pt.term_seq < $target_term_seq
    GROUP BY p.exam_id
),
hits AS (
    SELECT DISTINCT i.exam_id, i.topic_id
    FROM {{exam_items}} AS i
    JOIN prior AS p ON p.exam_id = i.exam_id
    WHERE @VISIBLE(i.term_seq, i.session)
)
SELECT h.topic_id,
       sum(wt.weight) / (SELECT sum(w2.weight) FROM weighted AS w2) AS x1
FROM hits AS h
JOIN weighted AS wt ON wt.exam_id = h.exam_id
GROUP BY h.topic_id
ORDER BY h.topic_id
