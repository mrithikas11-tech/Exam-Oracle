-- x3  Homework echo.  Spec: kit/02-product/prediction-model.md, signal row x3. Computed on both feature sets
-- (D3: visibility decides). Homework tagged T00 (off-list, D6) is left out, so it never sets the max.
--
--   echoed(h) <=> an echo_pairs row links homework problem h to a prior exam problem with sim >= tau, where
--                 the homework AND the exam are both visible to T, and the exam is a non-sealed exam present
--                 in the run database (never T itself).
--   n(t)  = number of DISTINCT visible homework problems tagged t (homework_items) that are echoed
--   x3(t) = n(t) / max over topics of n(t');  no echoed homework at all -> no rows -> 0 for every topic.
--
--   sim is the reciprocal-rank-fused BM25 + vector score (contracts/ledger-schema.sql echo_pairs);
--   tau is passed per run and logged in run_signals' output.
-- Params: course, target_exam, target_term_seq, target_session, tau.  Returns (topic_id, x3).
WITH hw AS (
    SELECT DISTINCT h.hw_id, h.topic_id
    FROM {{homework_items}} AS h
    WHERE h.course = $course
      AND h.topic_id <> 'T00'
      AND @VISIBLE(h.term_seq, h.session)
),
echoed AS (
    SELECT DISTINCT p.hw_id
    FROM {{echo_pairs}} AS p
    WHERE p.course = $course
      AND p.sim >= $tau
      AND p.exam_id <> $target_exam
      AND @VISIBLE(p.hw_term_seq, p.hw_session)
      AND @VISIBLE(p.exam_term_seq, p.exam_session)
      AND p.exam_id IN (SELECT e.exam_id FROM {{exams}} AS e
                        WHERE NOT e.sealed AND @VISIBLE(e.term_seq, e.session))
),
counts AS (
    SELECT hw.topic_id, count(DISTINCT hw.hw_id) AS n
    FROM hw
    JOIN echoed ON echoed.hw_id = hw.hw_id
    GROUP BY hw.topic_id
)
SELECT c.topic_id,
       CAST(c.n AS DOUBLE) / CAST((SELECT max(c2.n) FROM counts AS c2) AS DOUBLE) AS x3
FROM counts AS c
ORDER BY c.topic_id
