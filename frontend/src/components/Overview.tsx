import { VERDICT_LABEL, VERDICT_ORDER } from "../labels";
import type { AutomationPlan, ProcessGraph, Verdict } from "../types";

interface Props {
  graph: ProcessGraph;
  plan: AutomationPlan;
  onPick: (stepId: string) => void;
}

// A step worth starting with has to be one a computer can actually take on.
const RUNNABLE: Verdict[] = ["fully_automatable", "automatable_with_control"];

/**
 * The answer, before the evidence for it.
 *
 * Overview first, details on demand: somebody arriving at a result wants to know
 * whether it is mostly safe before they want to know why, so the headline and
 * the counts come first and each step's reasoning waits further down the page.
 */
export function Overview({ graph, plan, onPick }: Props) {
  const counts = VERDICT_ORDER.map((verdict) => ({
    verdict,
    count: plan.assessments.filter((a) => a.verdict === verdict).length,
  })).filter((c) => c.count > 0);

  // The model names this, so it is only offered when the step can be handed
  // over. "Start here" on a step that stays with the person would be advice to
  // automate exactly the thing it should not.
  const win = graph.steps.find((s) => s.id === plan.biggest_win);
  const winVerdict = plan.assessments.find((a) => a.step_id === plan.biggest_win)?.verdict;
  const startWith = win && winVerdict && RUNNABLE.includes(winVerdict) ? win : null;

  const questions = graph.questions.length;

  return (
    <section className="card answer">
      <span className="card__label">The answer</span>
      <p className="answer__headline">{plan.headline}</p>

      {/* The same counts as the list beside it, drawn to scale. Hidden from
          screen readers, which get the numbers from the list instead. */}
      <div className="spread" aria-hidden="true">
        {counts.map(({ verdict, count }) => (
          <span
            key={verdict}
            className={`spread__part spread__part--${verdict}`}
            style={{ flexGrow: count }}
          />
        ))}
      </div>

      <ul className="tally">
        {counts.map(({ verdict, count }) => (
          <li key={verdict} className={`tally__item tally__item--${verdict}`}>
            <strong>{count}</strong> {VERDICT_LABEL[verdict].toLowerCase()}
          </li>
        ))}
      </ul>

      {(startWith || questions > 0) && (
        <ul className="answer__next">
          {startWith && (
            <li>
              Worth doing first:{" "}
              <button className="linkish" onClick={() => onPick(startWith.id)}>
                {startWith.name}
              </button>
            </li>
          )}
          {questions > 0 && (
            <li>
              <a href="#questions">
                {questions} {questions === 1 ? "question" : "questions"}
              </a>{" "}
              could change this. Answering redraws the map.
            </li>
          )}
        </ul>
      )}
    </section>
  );
}
