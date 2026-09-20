import type { AnalyseResponse, Example } from "./types";

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
