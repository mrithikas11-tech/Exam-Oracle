-- Baseline A, "study evenly".  Spec: kit/02-product/prediction-model.md "Scoring": rank topics by x4
-- (lecture time), take the top K. Run for every target (D3); an exam_history target lists nothing.
--
--   score(t) = x4(t) (same window and formula as x4_lecture_time.sql)
--   order    = score DESC, topic_id ASC;  rank_and_seal keeps the first K.
--   Topics with no lecture in the window are not listed, so an exam_history run (no visible lectures)
--   yields an empty list, as contracts/prediction.schema.json baselines.even describes.
-- Params: course, target_term_seq, target_session, win_from, win_to.  Returns (topic_id, x4), ordered.
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
ORDER BY x4 DESC, w.topic_id ASC
