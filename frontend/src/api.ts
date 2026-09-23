import type {
  AnalyseResponse,
  Answer,
  AutomationPlan,
  EffortInput,
  EffortSummary,
  Example,
  ProcessGraph,
  ShareCreated,
  SharedAnalysis,
} from "./types";

export async function fetchExamples(): Promise<Example[]> {
  const response = await fetch("/api/examples");
  if (!response.ok) throw new Error(`Could not load examples (${response.status})`);
  return (await response.json()).examples;
}

export async function analyse(
  description: string,
  replayCase?: string,
  answers: Answer[] = [],
): Promise<AnalyseResponse> {
  const response = await fetch("/api/analyse", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ description, case: replayCase ?? null, answers }),
  });

  if (response.status === 422) {
    throw new Error("That description is too short. Give it a sentence or two more.");
  }
  if (!response.ok) {
    throw new Error(`The server could not handle that (${response.status}).`);
  }
  return response.json();
}

export async function buildWorkflow(
  graph: ProcessGraph,
  plan: AutomationPlan | null,
): Promise<string> {
  const response = await fetch("/api/export/n8n", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ graph, plan }),
  });
  if (!response.ok) throw new Error(`Could not build the workflow (${response.status}).`);

  return JSON.stringify(await response.json(), null, 2);
}

/**
 * Put the workflow on the clipboard, ready to paste onto an n8n canvas.
 *
 * n8n reads workflow JSON straight from a paste, so this skips the file, the
 * download folder and the import dialog entirely. Two steps instead of five,
 * and nothing left in Downloads afterwards.
 */
export async function copyToClipboard(text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    // Refused, which happens without a secure context or a recent click. The
    // caller falls back to the download rather than leaving somebody stuck.
    return false;
  }
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

export async function calculateEffort(
  plan: AutomationPlan,
  effort: EffortInput,
): Promise<EffortSummary> {
  const response = await fetch("/api/effort", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ plan, effort }),
  });
  if (!response.ok) throw new Error(`Could not work that out (${response.status}).`);
  return response.json();
}

export async function createShare(
  graph: ProcessGraph,
  plan: AutomationPlan | null,
  effort: EffortInput | null,
): Promise<ShareCreated> {
  const response = await fetch("/api/share", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ graph, plan, effort }),
  });
  if (response.status === 413) {
    throw new Error("That analysis is too large to share.");
  }
  if (!response.ok) throw new Error(`Could not create the link (${response.status}).`);
  return response.json();
}

export async function fetchShare(id: string): Promise<SharedAnalysis> {
  const response = await fetch(`/api/share/${encodeURIComponent(id)}`);
  if (response.status === 404) {
    throw new Error("That link has expired or never existed. Shares last 30 days.");
  }
  if (!response.ok) throw new Error(`Could not open that link (${response.status}).`);
  return response.json();
}

/** The share id if we are on a /s/<id> URL, otherwise null. */
export function shareIdFromUrl(): string | null {
  const match = window.location.pathname.match(/^\/s\/([A-Za-z0-9_-]+)\/?$/);
  return match ? match[1] : null;
}
