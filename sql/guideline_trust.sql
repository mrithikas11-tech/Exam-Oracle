-- Trust weight per guideline kind, the multiplier of x7.  Spec: kit/02-product/student-layer.md
-- ("the trust weight for a guideline kind = fraction of match (partial = 0.5) across all tests so far").
--
-- Runs against the LEDGER, not the run database: trust is a lesson learned from earlier runs across ALL
-- courses (the cross-course transfer), read at run time exactly like the beta weights are.
--   "so far" = tests of runs with run_seq < the current run's run_seq; and for runs of the SAME course the
--   run's target exam must also be visible to T (time-ordering rule), so a replay or the cold-start twin of
--   T can never learn from T's own answer key.
--   score(verdict) = match 1, partial 0.5, miss 0;  trust(kind) = avg(score).  Kinds with no test are
--   absent here; oracle/run_signals.py gives them the default 0.5.
-- Params: run_seq, course, target_term_seq, target_session.  Returns (kind, n_tests, trust).
WITH tests AS (
    SELECT gl.kind,
           CASE t.verdict WHEN 'match' THEN 1.0 WHEN 'partial' THEN 0.5 ELSE 0.0 END AS score
    FROM {{guideline_tests}} AS t
    JOIN {{guidelines}} AS gl ON gl.guideline_id = t.guideline_id
    JOIN {{runs}} AS r ON r.run_id = t.run_id
    LEFT JOIN {{exams}} AS e ON e.exam_id = r.target_exam
    WHERE r.run_seq < $run_seq
      AND t.verdict IN ('match', 'partial', 'miss')
      AND (r.course <> $course
           OR (e.exam_id IS NOT NULL AND @VISIBLE(e.term_seq, e.session)))
)
SELECT kind, count(*) AS n_tests, CAST(avg(score) AS DOUBLE) AS trust
FROM tests
GROUP BY kind
ORDER BY kind
