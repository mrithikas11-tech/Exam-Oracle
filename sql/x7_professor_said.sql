-- x7  Professor said.  Spec: kit/02-product/prediction-model.md, signal row x7, and
-- kit/02-product/student-layer.md "Converting a guideline into a scoreable claim". Both feature sets.
--
--   g ranges over this course's guidelines that apply to T.exam_type and are visible to T (stated at
--   (source_term_seq, source_session) before T). Kind 'format' is never scored.
--   covers(g, t):  kind = cumulative                  -> every topic of the course
--                  kind = emphasis_window | coverage   -> t's taught span [s_from, s_to] overlaps
--                                                         [g.from_session, g.to_session] (a missing end is
--                                                         open: session 1 / end of term)
--   t's taught span = min/max session of t's visible lectures in T's term. When t has none (every
--   exam_history target, or a topic not taught yet) it falls back to the topic list's
--   [first_lecture, last_lecture], read as session numbers -- an approximation (OCW numbers lectures and
--   sessions alike except around exam sessions); the topic list is course structure shared by all terms.
--   trust(kind) = mean verdict score (match 1, partial 0.5, miss 0) over the guideline tests of that kind
--   from strictly earlier runs whose target T could see (guideline_trust.sql); 0.5 while a kind has no test.
--   x7(t) = max over covering g of trust(kind(g));  no covering guideline -> 0.
-- Params: course, exam_type, target_term_seq, target_session,
--         trust_cumulative, trust_emphasis_window, trust_coverage.  Returns (topic_id, x7).
WITH g AS (
    SELECT gl.kind, gl.from_session, gl.to_session
    FROM {{guidelines}} AS gl
    WHERE gl.course = $course
      AND gl.applies_to_exam_type = $exam_type
      AND gl.kind IN ('cumulative', 'emphasis_window', 'coverage')
      AND @VISIBLE(gl.source_term_seq, gl.source_session)
),
taught AS (
    SELECT l.topic_id, min(l."session") AS s_from, max(l."session") AS s_to
    FROM {{lectures}} AS l
    WHERE l.course = $course
      AND l.term_seq = $target_term_seq
      AND l.topic_id IS NOT NULL
      AND @VISIBLE(l.term_seq, l.session)
    GROUP BY l.topic_id
),
span AS (
    SELECT tp.topic_id,
           COALESCE(ta.s_from, tp.first_lecture) AS s_from,
           COALESCE(ta.s_to, tp.last_lecture) AS s_to
    FROM {{topics}} AS tp
    LEFT JOIN taught AS ta ON ta.topic_id = tp.topic_id
    WHERE tp.course = $course
),
covered AS (
    SELECT s.topic_id, g.kind
    FROM span AS s
    JOIN g ON g.kind = 'cumulative'
           OR (s.s_from IS NOT NULL AND s.s_to IS NOT NULL
               AND s.s_from <= COALESCE(g.to_session, 1000000)
               AND s.s_to >= COALESCE(g.from_session, 1))
)
SELECT c.topic_id,
       max(CASE c.kind WHEN 'cumulative' THEN $trust_cumulative
                       WHEN 'emphasis_window' THEN $trust_emphasis_window
                       WHEN 'coverage' THEN $trust_coverage
           END) AS x7
FROM covered AS c
GROUP BY c.topic_id
ORDER BY c.topic_id
