/**
 * Keeping what somebody typed, on their own machine and nowhere else.
 *
 * A description of how a business actually runs takes a few minutes to write,
 * and until now a refresh threw it away. After a request that times out, which
 * happens, that stings rather more than it should.
 *
 * Deliberately localStorage and not a server. There are no accounts here and
 * there should not be: holding descriptions of how real businesses run, tied to
 * identities, indefinitely, is a liability bought for nothing. This never leaves
 * the browser, it is never sent anywhere, and there is a button to wipe it.
 *
 * Every read and write is wrapped. Storage throws rather than returning null in
 * a private window, with site data blocked, and inside some embedded browsers,
 * and none of those are reasons for the page to stop working.
 */

/** Versioned, so changing the shape stored here can never crash a returning visitor. */
const KEY = "automation-architect:draft:v1";

/**
 * After a week, what you typed is not a draft you are coming back to, it is a
 * surprise. Especially when the text describes somebody's payroll.
 */
const KEEP_FOR_DAYS = 7;

export interface Draft {
  description: string;
  answers: Record<string, string>;
  savedAt: number;
}

export function loadDraft(): Draft | null {
  let raw: string | null = null;
  try {
    raw = window.localStorage.getItem(KEY);
  } catch {
    return null; // Blocked or unavailable. Nothing here is important enough to care.
  }
  if (!raw) return null;

  try {
    const parsed = JSON.parse(raw) as Partial<Draft>;
    if (typeof parsed?.description !== "string" || !parsed.description.trim()) return null;

    const age = Date.now() - (parsed.savedAt ?? 0);
    if (age > KEEP_FOR_DAYS * 24 * 60 * 60 * 1000) {
      clearDraft();
      return null;
    }

    return {
      description: parsed.description,
      // Anything could be in here after a hand edit or a half written version,
      // so only string values survive.
      answers: Object.fromEntries(
        Object.entries(parsed.answers ?? {}).filter(([, v]) => typeof v === "string"),
      ) as Record<string, string>,
      savedAt: parsed.savedAt ?? Date.now(),
    };
  } catch {
    clearDraft(); // Corrupt. Better gone than throwing on every visit.
    return null;
  }
}

export function saveDraft(description: string, answers: Record<string, string>): void {
  try {
    if (!description.trim()) {
      clearDraft();
      return;
    }
    window.localStorage.setItem(
      KEY,
      JSON.stringify({ description, answers, savedAt: Date.now() } satisfies Draft),
    );
  } catch {
    // Full, blocked, or refused. Losing a draft is a small thing; throwing here
    // would happen on a keystroke, which is not.
  }
}

export function clearDraft(): void {
  try {
    window.localStorage.removeItem(KEY);
  } catch {
    // Nothing useful to do, and nothing that depends on it having worked.
  }
}
