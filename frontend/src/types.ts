// Mirrors the Pydantic models in backend/app/schemas. Kept by hand rather than
// generated: the surface is small, and a generator is another moving part to
// explain. If it grows, generate it from the OpenAPI schema FastAPI already
// publishes at /openapi.json.

export type Verdict =
  | "fully_automatable"
  | "automatable_with_control"
  | "human_required"
  | "needs_more_info";

export type StepKind =
  | "read"
  | "extract"
  | "transform"
  | "decision"
  | "write"
  | "notify"
  | "judgement"
  | "wait";

export interface Step {
  id: string;
  name: string;
  description: string;
  kind: StepKind;
  system_id: string | null;
  iterates_over: string | null;
  assumption: string | null;
}

export interface Edge {
  from_step: string;
  to_step: string;
  condition: string | null;
}

export interface System {
  id: string;
  name: string;
  category: string;
}

export interface Question {
  id: string;
  question: string;
  why_it_matters: string;
  suggested_answers: string[];
}

export interface ProcessGraph {
  title: string;
  summary: string;
  trigger: { kind: string; description: string; first_step_id: string };
  systems: System[];
  steps: Step[];
  edges: Edge[];
  questions: Question[];
}

export interface Threshold {
  field: string;
  operator: string;
  value: string | null;
  currency: string | null;
}

export interface Control {
  kind: string;
  reason: string;
  addresses: string[];
  threshold: Threshold | null;
  who_approves: string | null;
}

export interface Blocker {
  kind: string;
  detail: string;
  workaround: string | null;
}

/** A known-good shape for the job being described, if the corpus covers it. */
export interface PlaybookMatch {
  id: string;
  title: string;
  body: string;
  score: number;
}

export interface PlaybookResponse {
  match: PlaybookMatch | null;
  retriever: string;
}

/** One article in the written library, listed so the page can say what it covers. */
export interface LibraryArticle {
  id: string;
  title: string;
  also_called: string[];
  body: string;
}

/** A reply to one of the questions a previous run asked. Sent back as fact. */
export interface Answer {
  question: string;
  answer: string;
}

/** A record of our code disagreeing with the model. Written only by the server. */
export interface Override {
  kind: "risk_added" | "verdict_downgraded" | "control_added";
  was: string;
  now: string;
  because: string;
}

export interface StepAssessment {
  step_id: string;
  verdict: Verdict;
  rationale: string;
  confidence: "high" | "medium" | "low";
  risks: string[];
  controls: Control[];
  blockers: Blocker[];
  // Absent on analyses shared before this existed, so never assume an array.
  overrides?: Override[];
}

export interface AutomationPlan {
  process_title: string;
  headline: string;
  biggest_win: string | null;
  assessments: StepAssessment[];
}

export interface Attempt {
  number: number;
  ok: boolean;
  errors: string[];
}

export interface AnalyseResponse {
  ok: boolean;
  model: string;
  graph: ProcessGraph | null;
  plan: AutomationPlan | null;
  extraction_attempts: Attempt[];
  assessment_attempts: Attempt[];
  error: string | null;
}

export interface Example {
  id: string;
  label: string;
  description: string;
  replayable: boolean;
}

export type Period = "day" | "working_day" | "week" | "month";

export interface EffortInput {
  times_per_period: number;
  period: Period;
  minutes_per_step: Record<string, number>;
}

export interface StepEffort {
  step_id: string;
  verdict: Verdict;
  minutes_each_time: number;
  hours_per_month: number;
  could_run_without_you: boolean;
}

export interface EffortSummary {
  runs_per_month: number;
  hours_per_month: number;
  hours_that_could_run_without_you: number;
  hours_that_still_need_you: number;
  percentage_automatable: number;
  steps: StepEffort[];
  caveat: string;
}

export interface ShareCreated {
  id: string;
  expires_at: string;
}

export interface SharedAnalysis {
  id: string;
  title: string;
  created_at: string;
  expires_at: string;
  graph: ProcessGraph;
  plan: AutomationPlan | null;
  effort: EffortInput | null;
}
