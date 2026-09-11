-- Leakage audit of a run database.  Spec: contracts/README.md (time-ordering rule), kit/BUILDER-RULES.md §4
-- and kit/02-product/data-sources-and-sealing.md sealing rule 2 ("the run asserts ... inside that database").
--
-- One row per check: (tbl, chk, n, first_key, last_key). n > 0 is leakage (oracle/leakage_check.py exits 4);
-- first_key / last_key are the smallest and largest offending row keys, as evidence.
--   not_visible          a dated row the target may not see: NOT (term_seq < T.term_seq OR
--                        (term_seq = T.term_seq AND coalesce(session, end of term) < T.session))
--   sealed_flag          an exams row marked sealed
--   target_row(s)        a row of the target exam itself
--   exam_not_in_run_db   an item / echo pair whose exam is not a (visible, non-sealed) exams row of the run
--                        database -- this is how sealed exams' items show up, since sealed exams are never copied
--   hw_not_visible       a homework_vec row whose hw_id is not a visible homework_items row
-- oracle/leakage_check.py adds one more check from the ledger: exam ids the LEDGER marks sealed.
-- Params: target_exam, target_term_seq, target_session.
SELECT 'lectures' AS tbl, 'not_visible' AS chk, count(*) AS n,
       min(concat_ws('|', l.course, l.term, CAST(l."session" AS VARCHAR))) AS first_key,
       max(concat_ws('|', l.course, l.term, CAST(l."session" AS VARCHAR))) AS last_key
FROM {{lectures}} AS l WHERE NOT @VISIBLE(l.term_seq, l.session)
UNION ALL
SELECT 'exams', 'not_visible', count(*), min(e.exam_id), max(e.exam_id)
FROM {{exams}} AS e WHERE NOT @VISIBLE(e.term_seq, e.session)
UNION ALL
SELECT 'exams', 'sealed_flag', count(*), min(e.exam_id), max(e.exam_id)
FROM {{exams}} AS e WHERE e.sealed
UNION ALL
SELECT 'exams', 'target_row', count(*), min(e.exam_id), max(e.exam_id)
FROM {{exams}} AS e WHERE e.exam_id = $target_exam
UNION ALL
SELECT 'exam_items', 'not_visible', count(*),
       min(concat_ws('|', i.exam_id, i.problem, i.topic_id)), max(concat_ws('|', i.exam_id, i.problem, i.topic_id))
FROM {{exam_items}} AS i WHERE NOT @VISIBLE(i.term_seq, i.session)
UNION ALL
SELECT 'exam_items', 'target_rows', count(*),
       min(concat_ws('|', i.exam_id, i.problem, i.topic_id)), max(concat_ws('|', i.exam_id, i.problem, i.topic_id))
FROM {{exam_items}} AS i WHERE i.exam_id = $target_exam
UNION ALL
SELECT 'exam_items', 'exam_not_in_run_db', count(*),
       min(concat_ws('|', i.exam_id, i.problem, i.topic_id)), max(concat_ws('|', i.exam_id, i.problem, i.topic_id))
FROM {{exam_items}} AS i WHERE i.exam_id NOT IN (SELECT e.exam_id FROM {{exams}} AS e)
UNION ALL
SELECT 'homework_items', 'not_visible', count(*),
       min(concat_ws('|', h.hw_id, h.topic_id)), max(concat_ws('|', h.hw_id, h.topic_id))
FROM {{homework_items}} AS h WHERE NOT @VISIBLE(h.term_seq, h.session)
UNION ALL
SELECT 'homework_vec', 'hw_not_visible', count(*), min(v.hw_id), max(v.hw_id)
FROM {{homework_vec}} AS v
WHERE v.hw_id NOT IN (SELECT h.hw_id FROM {{homework_items}} AS h WHERE @VISIBLE(h.term_seq, h.session))
UNION ALL
SELECT 'echo_pairs', 'hw_not_visible', count(*),
       min(concat_ws('|', p.hw_id, p.exam_id, p.problem)), max(concat_ws('|', p.hw_id, p.exam_id, p.problem))
FROM {{echo_pairs}} AS p WHERE NOT @VISIBLE(p.hw_term_seq, p.hw_session)
UNION ALL
SELECT 'echo_pairs', 'exam_not_visible', count(*),
       min(concat_ws('|', p.hw_id, p.exam_id, p.problem)), max(concat_ws('|', p.hw_id, p.exam_id, p.problem))
FROM {{echo_pairs}} AS p WHERE NOT @VISIBLE(p.exam_term_seq, p.exam_session)
UNION ALL
SELECT 'echo_pairs', 'target_rows', count(*),
       min(concat_ws('|', p.hw_id, p.exam_id, p.problem)), max(concat_ws('|', p.hw_id, p.exam_id, p.problem))
FROM {{echo_pairs}} AS p WHERE p.exam_id = $target_exam
UNION ALL
SELECT 'echo_pairs', 'exam_not_in_run_db', count(*),
       min(concat_ws('|', p.hw_id, p.exam_id, p.problem)), max(concat_ws('|', p.hw_id, p.exam_id, p.problem))
FROM {{echo_pairs}} AS p WHERE p.exam_id NOT IN (SELECT e.exam_id FROM {{exams}} AS e)
UNION ALL
SELECT 'guidelines', 'not_visible', count(*), min(g.guideline_id), max(g.guideline_id)
FROM {{guidelines}} AS g WHERE NOT @VISIBLE(g.source_term_seq, g.source_session)
