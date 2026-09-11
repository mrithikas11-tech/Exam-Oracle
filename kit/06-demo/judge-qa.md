# Judge Questions — Rehearsed Answers

**"Isn't this just counting what came up before?"**
That's Baseline B ("copy last exam"), and it's on every chart. The difference comes from homework echoes, untested recent material, how far to trust the professor's stated guidance, and lessons carried between courses — the cold-start comparison on 6.003 isolates the last one.

**"How do we know you didn't peek?"**
Every backtest ran in its own hotdata database that only contained earlier data, and each one checked that. The reveal prediction's hash was on screen hours before the reveal. A person labeled the answer key before the prediction. Sealed files are on the loader's skip list and never in the repo.

**"Couldn't the LLM have memorized MIT exams?"**
Possibly — so the LLM never predicts. It only tags problems with topics and writes explanations. Predictions come from a transparent seven-signal model whose weights you can see.

**"Isn't this cheating?"**
It uses only material professors publish so students can study from it. It tells you where to spend your hours; you still have to learn the material.

**"What do you store about students, and who can see it?"**
Only what they give us: preferences, syllabus, guidelines, feedback, exam date and hours. Each student has their own Cognee dataset and HydraDB collection; nobody else can read them. The demo students are fictional.

**"Where's the compounding?"**
Four places: points covered rises across runs; the cost to load a course falls once Rote replays it; first-try checks rise; and a student's next answer changes after feedback.

**"Why hosted HydraDB and not Cypher?"** (HydraDB judge)
RocketRide's HydraDB node speaks the hosted memory API, and it gives every student an isolated collection with preference inference — which is what our memory needs. [If built:] We also run the open-source HydraDB for the course graph and use `algo.MSpaths` for the "why" paths.

**"What does Rote actually do here?"** (Modiqo judge)
Loading an MIT course and running a backtest are recorded once as Plays and replayed with new inputs. Replays cost near-zero model tokens; failures are named with evidence and resumed; `load-course` is published in the Playoffs catalog.

**"Why RocketRide?"** (RocketRide judge)
The whole student app runs as RocketRide pipelines using your Cognee, HydraDB and hotdata nodes and the Wave agent; tracing shows token use per call.

**"What does hotdata add over a normal database?"** (hotdata judge)
Throwaway databases in under a second: every backtest gets its own, holding only data from before the exam — leakage prevention by construction — plus saved, versioned signal queries and hybrid search for homework echoes.

**"What does Cognee add?"** (Cognee judge)
It turns messy course documents onto a fixed topic list (strict ontology, custom models), keeps shared course and private student memory with permissions, and turns scored sessions and feedback into lessons and preferences.

**"Who pays?"**
Students and tutoring companies first; and professors: "your exam tests topic X three times more than your lectures did."

**"What would you build next?"**
Real student pilots with consent; more courses to strengthen cross-course lessons; professor-facing exam balance reports.
