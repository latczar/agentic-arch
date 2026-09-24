import type { Verdict } from "./types";

/**
 * One wording for each verdict, wherever a verdict is named.
 *
 * The key on the first screen, the diagram and the verdict list used to carry
 * their own copies, and a reader who learns "needs a guard" from the key should
 * find exactly those words on the step.
 */
export const VERDICT_LABEL: Record<Verdict, string> = {
  fully_automatable: "Runs itself",
  automatable_with_control: "Needs a guard",
  human_required: "Stays with you",
  needs_more_info: "Unclear",
};

/**
 * An override's before and after, in the words the rest of the page uses.
 *
 * The server records them as it thinks of them: "fully_automatable",
 * "moves_money, irreversible", "human_approval". Accurate, and not what anybody
 * reading the page should have to decode.
 */
export function plainly(value: string): string {
  return value
    .split(", ")
    .map((part) => VERDICT_LABEL[part as Verdict]?.toLowerCase() ?? part.replace(/_/g, " "))
    .join(", ");
}
