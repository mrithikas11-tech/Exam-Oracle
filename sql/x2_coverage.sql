-- x2  Coverage.  Spec: kit/02-product/prediction-model.md, signal row x2. Full feature set only
-- (exam_history targets cannot see the published term's lectures: contracts/README.md).
--
--   x2(t) = #(t's visible lectures in T's term with session in [win_from, win_to])
--           / #(t's visible lectures in T's term)
--
--   [win_from, win_to] is T's coverage window from coverage_window.sql: the stated coverage when given,
--   else for a cumulative exam (finals) sessions 1..T.session-1 -- so every topic with a visible lecture
--   scores 1.0 -- else the sessions after the previous exam of T's term up to T.session-1.
--   A topic with no visible lecture in T's term gets no row -> 0.
-- Params: course, target_term_seq, target_session, win_from, win_to.  Returns (topic_id, x2).
SELECT l.topic_id,
       CAST(sum(CASE WHEN l."session" BETWEEN $win_from AND $win_to THEN 1 ELSE 0 END) AS DOUBLE)
         / CAST(count(*) AS DOUBLE) AS x2
FROM {{lectures}} AS l
WHERE l.course = $course
  AND l.term_seq = $target_term_seq
  AND l.topic_id IS NOT NULL
  AND @VISIBLE(l.term_seq, l.session)
GROUP BY l.topic_id
ORDER BY l.topic_id
