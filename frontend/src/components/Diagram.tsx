import { useMemo } from "react";
import {
  Background,
  Controls,
  ReactFlow,
  type Edge as FlowEdge,
  type Node as FlowNode,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";

import { layoutGraph } from "../layout";
import { StepNode, type StepNodeData } from "./StepNode";
import type { AutomationPlan, ProcessGraph } from "../types";

const nodeTypes = { step: StepNode };

interface Props {
  graph: ProcessGraph;
  plan: AutomationPlan | null;
  selected: string | null;
  onSelect: (stepId: string | null) => void;
}

export function Diagram({ graph, plan, selected, onSelect }: Props) {
  const { nodes, edges } = useMemo(() => {
    const verdictFor = (stepId: string) =>
      plan?.assessments.find((a) => a.step_id === stepId) ?? null;

    const raw: FlowNode[] = graph.steps.map((step) => {
      const assessment = verdictFor(step.id);
      const data: StepNodeData = {
        label: step.name,
        kind: step.kind,
        verdict: assessment?.verdict ?? null,
        riskCount: assessment?.risks.length ?? 0,
        hasControl: (assessment?.controls.length ?? 0) > 0,
        selected: selected === step.id,
      };
      return { id: step.id, type: "step", position: { x: 0, y: 0 }, data };
    });

    const flowEdges: FlowEdge[] = graph.edges.map((edge, index) => ({
      id: `${edge.from_step}-${edge.to_step}-${index}`,
      source: edge.from_step,
      target: edge.to_step,
      label: edge.condition ?? undefined,
      animated: false,
      labelBgPadding: [6, 3],
      labelBgBorderRadius: 4,
    }));

    return { nodes: layoutGraph(raw, flowEdges), edges: flowEdges };
  }, [graph, plan, selected]);

  return (
    <div className="diagram">
      <ReactFlow
        nodes={nodes}
        edges={edges}
        nodeTypes={nodeTypes}
        fitView
        fitViewOptions={{ padding: 0.15 }}
        proOptions={{ hideAttribution: false }}
        onNodeClick={(_, node) => onSelect(node.id)}
        onPaneClick={() => onSelect(null)}
        nodesDraggable={false}
        nodesConnectable={false}
        // By default the canvas swallows the mouse wheel and zooms. On a page
        // this tall that traps the reader: they scroll, the diagram zooms, and
        // the page stays put. Let the wheel scroll the page and keep zoom on
        // the controls, pinch, and ctrl-scroll.
        zoomOnScroll={false}
        panOnScroll={false}
        preventScrolling={false}
        zoomActivationKeyCode="Control"
      >
        <Background gap={20} size={1} />
        <Controls showInteractive={false} />
      </ReactFlow>
    </div>
  );
}
