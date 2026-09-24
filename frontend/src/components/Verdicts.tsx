import { useEffect, useRef } from "react";

import { plainly, VERDICT_LABEL as LABEL, VERDICT_ORDER as ORDER } from "../labels";
import type { AutomationPlan, Override, ProcessGraph } from "../types";

const OVERRIDE_KIND: Record<Override["kind"], string> = {
  risk_added: "Risk added",
  verdict_downgraded: "Verdict lowered",
  control_added: "Guard added",
};

interface Props {
  graph: ProcessGraph;
  plan: AutomationPlan;
  selected: string | null;
  onSelect: (stepId: string | null) => void;
}

export function Verdicts({ graph, plan, selected, onSelect }: Props) {
  const nameOf = (id: string) =>
    graph.steps.find((s) => s.id === id)?.name ?? id;

  // Picking a step in the diagram, or from "worth doing first", brings its
  // verdict into view. Only when it is off screen: a card already in sight
  // jumping about under the pointer is worse than not moving at all.
  const list = useRef<HTMLElement>(null);
  useEffect(() => {
    if (!selected) return;
    const card = list.current?.querySelector<HTMLElement>(`[data-step="${CSS.escape(selected)}"]`);
    if (!card) return;
    const box = card.getBoundingClientRect();
    if (box.top < 72 || box.bottom > window.innerHeight) {
      card.scrollIntoView({ block: "center" });
    }
  }, [selected]);

  // Anything needing attention first. A list that opens with six green rows
  // buries the one thing the reader actually has to decide about.
  const sorted = [...plan.assessments].sort(
    (a, b) => ORDER.indexOf(a.verdict) - ORDER.indexOf(b.verdict),
  );

  return (
    <section className="verdicts" ref={list}>
      {sorted.map((assessment) => (
        <article
          key={assessment.step_id}
          data-step={assessment.step_id}
          className={`verdict verdict--${assessment.verdict} ${
            selected === assessment.step_id ? "verdict--active" : ""
          }`}
          onClick={() => onSelect(assessment.step_id)}
        >
          <header>
            <span className="verdict__tag">{LABEL[assessment.verdict]}</span>
            <h3>{nameOf(assessment.step_id)}</h3>
          </header>

          <p>{assessment.rationale}</p>

          {/* The one thing on this page that is ours rather than the model's.
              Without it a verdict we corrected and a verdict it got right look
              exactly the same, and the correction is the whole point. */}
          {(assessment.overrides ?? []).length > 0 && (
            <div className="override">
              <strong className="override__title">
                We overruled the model on this step
              </strong>
              {(assessment.overrides ?? []).map((override, index) => (
                <p key={index} className="override__line">
                  <span className="override__change">
                    <span className="override__kind">{OVERRIDE_KIND[override.kind]}</span>
                    <s className="override__was">{plainly(override.was)}</s>{" "}
                    <span aria-hidden="true">&rarr;</span>{" "}
                    <strong className="override__now">{plainly(override.now)}</strong>
                  </span>
                  {override.because}
                </p>
              ))}
            </div>
          )}

          {assessment.risks.length > 0 && (
            <p className="verdict__risks">
              {assessment.risks.map((risk) => (
                <span key={risk} className="chip">
                  {risk.replace(/_/g, " ")}
                </span>
              ))}
            </p>
          )}

          {assessment.controls.map((control, index) => (
            <div key={index} className="control">
              <strong>{control.kind.replace(/_/g, " ")}</strong>
              {control.threshold && (
                <span className="control__limit">
                  {" "}
                  when {control.threshold.field} {control.threshold.operator}{" "}
                  {control.threshold.value} {control.threshold.currency ?? ""}
                </span>
              )}
              <p>{control.reason}</p>
              {control.who_approves && <p className="control__who">Approver: {control.who_approves}</p>}
            </div>
          ))}

          {assessment.blockers.map((blocker, index) => (
            <div key={index} className="blocker">
              <strong>{blocker.kind.replace(/_/g, " ")}</strong>
              <p>{blocker.detail}</p>
            </div>
          ))}
        </article>
      ))}
    </section>
  );
}
