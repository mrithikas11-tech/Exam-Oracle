# Glossary

| Term | Meaning here |
|---|---|
| Backtest / run | Predicting one past exam using only what a student would have had before it, then scoring it |
| Baseline A ("study evenly") | Rank topics by lecture time; take the top K |
| Baseline B ("copy last exam") | Topics of the most recent exam of the same type |
| Brier score | Mean squared error between predicted probabilities and what appeared (lower is better) |
| Cold-start comparison | Course 3's first prediction with learned lessons vs with blank weights — proves cross-course transfer |
| Degraded (Rote) | A step that completed with a warning (exit 0 + warning JSON) — the Play continues |
| Hard fault (Rote) | A failed step; dependents BLOCKED; `--resume` continues after the fix |
| Homework echo | An exam problem that closely resembles an earlier homework problem |
| K | Size of the study list: median number of topics on past exams of that type |
| Leakage | Any way future information (the target exam or later material) reaches a prediction |
| Ledger | The permanent hotdata database holding all facts and the run log |
| Lessons | The learned signal weights (β), each stored with its evidence and a one-line statement |
| M1 / M2 / M3 | Milestones: first scored prediction; sealed reveal prediction; backup video |
| Play | A Rote artifact: a compiled, typed-input step graph replayed without re-reasoning |
| Points covered | Share of the exam's points whose topics were on the study list (headline score) |
| Professor said (x7) | Signal from stated syllabus guidelines, weighted by how often such guidance matched real exams |
| Projector | Optional script copying Cognee's graph into open-source HydraDB with Cypher bulk writes |
| Run database | A throwaway hotdata database (`run-<id>`) with only pre-exam rows; expires in 2 h |
| Sealed | Kept outside the repo and out of every code path until scoring or the reveal |
| Skip list | URLs and hashes the loader must refuse (sealed exams) |
| Strict ontology | Cognee mode that drops any tag not on the course's topic list |
| Topic list | The fixed 12–20 topics per course, set before any prediction |
| Wave | RocketRide's native agent node (`agent_rocketride`) |
