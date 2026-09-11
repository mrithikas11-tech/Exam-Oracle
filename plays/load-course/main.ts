#!/usr/bin/env -S rote play run
/**
 * @rote-frontmatter
 * ---
 * name: load-course
 * source: https://github.com/mrithikas11-tech/Exam-Oracle
 * description: 'Load one MIT OpenCourseWare course into Exam Oracle: index its exams, solutions, problem sets and lecture notes; download each PDF hash-named (sealed exams refused, exit 2); extract text; split exams into numbered problems with points; validate numbering and point totals (hard fault, exit 3); load the course structure (data/courses/<course>/) and homework into the ledger (contracts/ledger-schema.sql); tag exam problems onto the fixed topic list with Cognee; record a course_loads row; notify RocketRide. Needs the exam-oracle CLI from github.com/mrithikas11-tech/Exam-Oracle (set ORACLE_REPO_ROOT to a checkout unless installed editable), ORACLE_DATA_DIR, and LLM_API_KEY for tagging.'
 * provenance:
 *   author: mrithikas11@gmail.com
 * metadata:
 *   rote_version: 0.82.0
 *   version: 0.1.2
 *   status: released
 *   kind: atomic
 *   flow_type: parallel
 *   execution_model: steps_with_presentation
 *   requires_endpoints: []
 *   requires_sessions: false
 *   discoverability:
 *     tags:
 *     - exam-oracle
 *     - ocw
 *     - hotdata
 *   exploration_model: null
 *   contract:
 *     atomic: true
 *     input:
 *       type: none
 *     output:
 *       format: json
 *       destination: stdout
 *     composable: true
 * parameters:
 * - name: course
 *   param_type: string
 *   required: true
 *   default: null
 *   description: OCW course code with a dot, e.g. 6.003 (names the data folders and ledger rows)
 *   example: '6.003'
 *   valid_values: null
 * - name: course_url
 *   param_type: string
 *   required: true
 *   default: null
 *   description: Any https://ocw.mit.edu/courses/<slug>/ URL of the course; its exams, assignments and lecture-notes pages are indexed
 *   example: https://ocw.mit.edu/courses/6-003-signals-and-systems-fall-2011/
 *   valid_values: null
 * - name: skip_list
 *   param_type: string
 *   required: false
 *   default: builtin
 *   description: Sealed-exam skip list; 'builtin' uses the list shipped in the exam-oracle package
 *   example: builtin
 *   valid_values: null
 * - name: mode
 *   param_type: string
 *   required: false
 *   default: replay
 *   description: Recorded in ledger.course_loads - 'agent' for the first, agent-driven load; 'replay' for Play replays
 *   example: replay
 *   valid_values:
 *   - agent
 *   - replay
 * steps:
 *   index:
 *     type: process.exec
 *     argv: [exam-oracle, index, --course, $course, --course-url, $course_url, --skip-list, $skip_list]
 *     timeout_ms: 120000
 *   download:
 *     type: process.exec
 *     depends_on: [index]
 *     for_each: '$.stdout.text | fromjson | .docs'
 *     max_concurrency: 4
 *     argv: [exam-oracle, download, --doc, $item, --skip-list, $skip_list]
 *     timeout_ms: 180000
 *   extract:
 *     type: process.exec
 *     depends_on: [download]
 *     argv: [exam-oracle, extract, --course, $course]
 *     timeout_ms: 900000
 *   split:
 *     type: process.exec
 *     depends_on: [extract]
 *     argv: [exam-oracle, split, --course, $course]
 *     timeout_ms: 120000
 *   validate:
 *     type: process.exec
 *     depends_on: [split]
 *     argv: [exam-oracle, validate, --course, $course]
 *     timeout_ms: 60000
 *   structure:
 *     type: process.exec
 *     argv: [exam-oracle, structure, --course, $course]
 *     timeout_ms: 300000
 *   items:
 *     type: process.exec
 *     depends_on: [validate]
 *     argv: [exam-oracle, items, --course, $course]
 *     timeout_ms: 120000
 *   tag:
 *     type: process.exec
 *     depends_on: [items, structure]
 *     argv: [exam-oracle, tag, --course, $course]
 *     timeout_ms: 3600000
 *   load:
 *     type: process.exec
 *     depends_on: [validate, structure]
 *     argv: [exam-oracle, load, --course, $course]
 *     timeout_ms: 600000
 *   summary:
 *     type: process.exec
 *     depends_on: [tag, load]
 *     argv: [exam-oracle, summary, --course, $course, --mode, $mode]
 *     timeout_ms: 300000
 *   notify:
 *     type: process.exec
 *     depends_on: [summary]
 *     argv: [exam-oracle, notify, --course, $course]
 *     timeout_ms: 60000
 * ---
 */

const presentationSdk = await import("__ROTE_PRESENTATION_SDK__").catch((cause) => {
  throw new Error(
    "This is a rote steps presentation program. Run it with `rote play run <name>`.",
    { cause },
  );
});
const { FlowOutput, isProcessExecBody, loadPresentationContext, stepName } = presentationSdk;

const out = new FlowOutput();
const ctx = await loadPresentationContext();
out.setRunStatus(ctx.run.status);

// Takes the step handle (not the name) so every `stepName("...")` at the
// call sites stays a literal that lint can verify against `steps:`.
function renderStep(step: ReturnType<typeof ctx.step>): unknown {
  switch (step.outcome.status) {
    case "completed":
      return step.outcome.output.body;
    case "restored": {
      const source = step.outcome.output.source;
      if (source?.status === "partial") {
        return {
          status: "partial",
          body: step.outcome.output.body,
          diagnostics: source.diagnostics,
          additional_diagnostics: source.additional_diagnostics,
        };
      }
      return step.outcome.output.body;
    }
    case "partial":
      return {
        status: "partial",
        body: step.outcome.output.output.body,
        diagnostics: step.outcome.output.diagnostics,
      };
    case "skipped":
      return { status: "skipped", reason: step.outcome.output.reason };
    case "failed":
      return { status: "failed", message: step.outcome.output.message };
    case "blocked":
      return {
        status: "blocked",
        reason: step.outcome.output.reason,
        blocked_by: step.outcome.output.blocked_by ?? [],
      };
    default:
      throw new Error(
        `unsupported step outcome: ${JSON.stringify(step.outcome)}. ` +
          `Re-export the play to regenerate this switch.`,
      );
  }
}

// Every exam-oracle command prints one JSON object on stdout; read it when the step finished.
function stdoutJson(step: ReturnType<typeof ctx.step>): Record<string, unknown> | undefined {
  const status = step.outcome.status;
  if (status !== "completed" && status !== "restored") return undefined;
  const body = step.outcome.output.body;
  if (!isProcessExecBody(body)) return undefined;
  const text = body.stdout?.text;
  if (text === undefined || text.trim() === "") return undefined;
  try {
    const value: unknown = JSON.parse(text);
    return typeof value === "object" && value !== null ? value as Record<string, unknown> : undefined;
  } catch {
    return undefined;
  }
}

const handles = {
  index: ctx.step(stepName("index")),
  download: ctx.step(stepName("download")),
  extract: ctx.step(stepName("extract")),
  split: ctx.step(stepName("split")),
  validate: ctx.step(stepName("validate")),
  structure: ctx.step(stepName("structure")),
  items: ctx.step(stepName("items")),
  tag: ctx.step(stepName("tag")),
  load: ctx.step(stepName("load")),
  summary: ctx.step(stepName("summary")),
  notify: ctx.step(stepName("notify")),
};

const statuses: Record<string, string> = {};
const renderedSteps: Record<string, unknown> = {};
const warnings: string[] = [];
for (const [name, handle] of Object.entries(handles)) {
  statuses[name] = handle.outcome.status;
  if (name === "index" || name === "download") {
    renderedSteps[name] = { status: handle.outcome.status };
    continue;
  }
  renderedSteps[name] = renderStep(handle);
  const parsed = stdoutJson(handle);
  if (parsed && typeof parsed.warning === "string") warnings.push(`${name}: ${parsed.warning}`);
}

const course = typeof ctx.params.course === "string" ? ctx.params.course : "?";
const summary = stdoutJson(handles.summary);
const notOk = Object.entries(statuses).filter(([, s]) => s !== "completed" && s !== "restored");

const headlinePrefix = (() => {
  switch (ctx.run.status) {
    case "succeeded":
      return "";
    case "partial":
      return "INCOMPLETE: ";
    case "failed":
      return "FAILED: ";
  }
})();

const headline = summary
  ? `${headlinePrefix}${course}: ${summary.items} problems loaded (${summary.exam_items_rows ?? 0} tagged exam rows) from ${summary.docs} documents in ` +
    `${summary.seconds}s (${summary.degraded} degraded, ${summary.sealed_excluded} sealed excluded, ` +
    `checks first try: ${summary.checks_first_try})`
  : `${headlinePrefix}${course}: load did not finish; ` +
    notOk.map(([name, s]) => `${name}=${s}`).join(", ");

const lines = [`# load-course ${course}`, headline];
if (notOk.length > 0) lines.push(`Steps not completed: ${notOk.map(([n, s]) => `${n} (${s})`).join(", ")}`);
if (warnings.length > 0) lines.push("Degraded:", ...warnings.map((w) => `- ${w}`));
out.human(lines.join("\n"));
out.summary(headline);
out.result({
  run_id: ctx.run.run_id,
  status: ctx.run.status,
  complete: ctx.run.status === "succeeded",
  course,
  summary: summary ?? null,
  warnings,
  step_status: statuses,
  steps: renderedSteps,
});
