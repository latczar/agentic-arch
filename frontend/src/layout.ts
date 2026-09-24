import dagre from "@dagrejs/dagre";
import type { Node, Edge } from "@xyflow/react";

// Kept in step with .node in styles.css. Dagre spaces the boxes by these
// numbers, so a node drawn wider or taller than it was told about crowds its
// neighbours and pulls the edges off centre.
export const NODE_WIDTH = 240;
export const NODE_HEIGHT = 88;

// React Flow positions nodes wherever you tell it to and has no opinion about
// layout, so something has to work out where they go. Dagre is the boring
// choice: it handles layering and keeps branches from crossing, which is most
// of what a process diagram needs.
export function layoutGraph(nodes: Node[], edges: Edge[]): Node[] {
  const graph = new dagre.graphlib.Graph();
  graph.setDefaultEdgeLabel(() => ({}));
  graph.setGraph({ rankdir: "TB", nodesep: 40, ranksep: 44, marginx: 16, marginy: 16 });

  nodes.forEach((node) =>
    graph.setNode(node.id, { width: NODE_WIDTH, height: NODE_HEIGHT }),
  );
  edges.forEach((edge) => graph.setEdge(edge.source, edge.target));

  dagre.layout(graph);

  return nodes.map((node) => {
    const placed = graph.node(node.id);
    return {
      ...node,
      // Dagre gives a centre point, React Flow wants a top-left corner.
      position: { x: placed.x - NODE_WIDTH / 2, y: placed.y - NODE_HEIGHT / 2 },
    };
  });
}
