import { Handle, Position } from "@xyflow/react";
import type { NodeProps } from "@xyflow/react";
import type { StepKind, Verdict } from "../types";

export interface StepNodeData extends Record<string, unknown> {
  label: string;
  kind: StepKind;
  verdict: Verdict | null;
  riskCount: number;
  hasControl: boolean;
  selected: boolean;
}

const VERDICT_LABEL: Record<Verdict, string> = {
  fully_automatable: "runs itself",
  automatable_with_control: "needs a guard",
  human_required: "stays with you",
  needs_more_info: "unclear",
};

export function StepNode({ data }: NodeProps) {
  const step = data as StepNodeData;
  const verdict = step.verdict ?? "needs_more_info";

  return (
    <div
      className={`node node--${verdict} ${step.selected ? "node--active" : ""}`}
      data-decision={step.kind === "decision"}
    >
      <Handle type="target" position={Position.Top} />
      <div className="node__kind">{step.kind}</div>
      <div className="node__label">{step.label}</div>
      <div className="node__foot">
        <span className="node__verdict">{VERDICT_LABEL[verdict]}</span>
        {step.hasControl && <span className="node__badge" title="Has a guard">guard</span>}
      </div>
      <Handle type="source" position={Position.Bottom} />
    </div>
  );
}
