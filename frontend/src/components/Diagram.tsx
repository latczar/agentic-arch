import { useEffect, useMemo, useRef, type CSSProperties } from "react";
import {
  Background,
  Controls,
  ReactFlow,
  ReactFlowProvider,
  useReactFlow,
  type Edge as FlowEdge,
  type Node as FlowNode,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";

import { layoutGraph, NODE_HEIGHT } from "../layout";
import { StepNode, type StepNodeData } from "./StepNode";
import type { AutomationPlan, ProcessGraph } from "../types";

const nodeTypes = { step: StepNode };

// Never past full size. A three-step process would otherwise be blown up to
// fill the box, and text at 180% looks like a mistake rather than a diagram.
const FIT = { padding: 0.08, maxZoom: 1 };

// The tallest the box gets, and the shortest. In between it takes the height
// of the process, so a short one is not a few boxes floating in a large empty
// frame and a long one is not shrunk further than it has to be.
const TALLEST = 760;
const SHORTEST = 300;

interface Props {
  graph: ProcessGraph;
  plan: AutomationPlan | null;
  selected: string | null;
  onSelect: (stepId: string | null) => void;
}

/**
 * Re-fit the diagram when the panel changes size.
 *
 * `fitView` only runs once, on mount. Everything after that leaves the graph
 * framed for a width the panel no longer has, so resizing a window pushes the
 * process into a corner with an empty half beside it. Common enough to be worth
 * the observer: the panel is half a two-column grid that reflows at 900px.
 */
function RefitOnResize() {
  const { fitView } = useReactFlow();
  const frame = useRef(0);

  useEffect(() => {
    const parent = document.querySelector(".diagram");
    if (!parent) return;

    const observer = new ResizeObserver(() => {
      // Coalesced into the next frame. A drag-resize fires this continuously,
      // and re-fitting on every pixel is work nobody sees.
      cancelAnimationFrame(frame.current);
      frame.current = requestAnimationFrame(() => fitView(FIT));
    });

    observer.observe(parent);
    return () => {
      cancelAnimationFrame(frame.current);
      observer.disconnect();
    };
  }, [fitView]);

  return null;
}

export function Diagram(props: Props) {
  return (
    <ReactFlowProvider>
      <Canvas {...props} />
    </ReactFlowProvider>
  );
}

function Canvas({ graph, plan, selected, onSelect }: Props) {
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

  // How tall the process is at full size, plus the fit padding and the margin
  // dagre leaves. The stylesheet caps it by the window as well, because this is
  // pinned beside the verdicts and has to fit on the screen to be any use.
  const drawn = Math.max(0, ...nodes.map((node) => node.position.y)) + NODE_HEIGHT;
  const fits = Math.min(TALLEST, Math.max(SHORTEST, Math.round(drawn * 1.2 + 32)));

  return (
    <div className="diagram" style={{ "--diagram-fits": `${fits}px` } as CSSProperties}>
      <ReactFlow
        nodes={nodes}
        edges={edges}
        nodeTypes={nodeTypes}
        fitView
        fitViewOptions={FIT}
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
        <RefitOnResize />
      </ReactFlow>
    </div>
  );
}
