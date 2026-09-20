import type { AnalyseResponse, AutomationPlan, Example, ProcessGraph } from "./types";

export async function fetchExamples(): Promise<Example[]> {
  const response = await fetch("/api/examples");
  if (!response.ok) throw new Error(`Could not load examples (${response.status})`);
  return (await response.json()).examples;
}

export async function analyse(
  description: string,
  replayCase?: string,
): Promise<AnalyseResponse> {
  const response = await fetch("/api/analyse", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ description, case: replayCase ?? null }),
  });

  if (response.status === 422) {
    throw new Error("That description is too short. Give it a sentence or two more.");
  }
  if (!response.ok) {
    throw new Error(`The server could not handle that (${response.status}).`);
  }
  return response.json();
}

export async function exportN8n(
  graph: ProcessGraph,
  plan: AutomationPlan | null,
): Promise<Blob> {
  const response = await fetch("/api/export/n8n", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ graph, plan }),
  });
  if (!response.ok) throw new Error(`Could not build the export (${response.status}).`);

  const workflow = await response.json();
  return new Blob([JSON.stringify(workflow, null, 2)], { type: "application/json" });
}

export function download(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;

  // Two things this needs that are easy to get wrong. A link that is not in the
  // document does not reliably start a download, and revoking the object URL in
  // the same tick cancels it before the browser has finished reading the blob.
  document.body.appendChild(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 10_000);
}
