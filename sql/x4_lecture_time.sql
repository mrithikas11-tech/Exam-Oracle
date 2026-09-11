-- x4  Lecture time.  Spec: kit/02-product/prediction-model.md, signal row x4. Full feature set only.
--
--   x4(t) = #(visible lectures on t in T's term with session in [win_from, win_to])
--           / #(visible lectures in T's term with session in [win_from, win_to])
--
--   The denominator counts EVERY lecture in the window, including lectures with no topic (reviews), so
--   sum_t x4(t) <= 1. Same window as x2 (coverage_window.sql). No lecture in the window -> no rows -> 0.
-- Params: course, target_term_seq, target_session, win_from, win_to.  Returns (topic_id, x4).
WITH win AS (
    SELECT l.topic_id
    FROM {{lectures}} AS l
    WHERE l.course = $course
      AND l.term_seq = $target_term_seq
      AND l."session" BETWEEN $win_from AND $win_to
      AND @VISIBLE(l.term_seq, l.session)
)
SELECT w.topic_id,
       CAST(count(*) AS DOUBLE) / CAST((SELECT count(*) FROM win AS w2) AS DOUBLE) AS x4
FROM win AS w
WHERE w.topic_id IS NOT NULL
GROUP BY w.topic_id
ORDER BY w.topic_id
