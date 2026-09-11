-- Coverage window of the target exam T (used by x2, x4 and baseline_even). Full feature set only.
-- Spec: kit/02-product/prediction-model.md row x2 ("quizzes: window from the calendar or syllabus; finals:
-- cumulative"); contracts/ledger-schema.sql exams.coverage_from_session / coverage_to_session / cumulative.
--
--   1. T states coverage (stated_from / stated_to = T's exams.coverage_from/to_session, NULL if not stated):
--        [coalesce(stated_from, 1), min(coalesce(stated_to, T.session - 1), T.session - 1)]   source = stated
--   2. else T is cumulative (exams.cumulative; NULL counts as TRUE for finals, decided in run_context.py):
--        [1, T.session - 1], i.e. every visible lecture of T's term                          source = cumulative
--   3. else the sessions after the previous visible exam of T's term, up to T.session - 1:
--        [coalesce(max(previous exam session), 0) + 1, T.session - 1]                   source = since_previous_exam
--   The upper end never passes T.session - 1: lectures at or after T are not visible (time-ordering rule).
--   An unknown T.session is end of term (1000000), so the window then runs to the term's last session.
-- Params: course, target_exam, target_term_seq, target_session, stated_from, stated_to, cumulative.
-- Returns one row: (win_from, win_to, window_source).
WITH prev AS (
    SELECT max(e."session") AS s
    FROM {{exams}} AS e
    WHERE e.course = $course
      AND e.term_seq = $target_term_seq
      AND e.exam_id <> $target_exam
      AND e."session" IS NOT NULL
      AND @VISIBLE(e.term_seq, e.session)
),
stated AS (
    SELECT CAST($stated_from AS INTEGER) AS f, CAST($stated_to AS INTEGER) AS t
)
SELECT
    CASE WHEN st.f IS NOT NULL OR st.t IS NOT NULL THEN COALESCE(st.f, 1)
         WHEN $cumulative THEN 1
         ELSE COALESCE((SELECT p.s FROM prev AS p), 0) + 1
    END AS win_from,
    CASE WHEN st.t IS NOT NULL THEN least(st.t, $target_session - 1)
         ELSE $target_session - 1
    END AS win_to,
    CASE WHEN st.f IS NOT NULL OR st.t IS NOT NULL THEN 'stated'
         WHEN $cumulative THEN 'cumulative'
         ELSE 'since_previous_exam'
    END AS window_source
FROM stated AS st
