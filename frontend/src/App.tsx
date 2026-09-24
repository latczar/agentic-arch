import { useCallback, useEffect, useState } from "react";

import {
  analyse,
  buildWorkflow,
  copyToClipboard,
  createShare,
  download,
  fetchExamples,
  fetchLibrary,
  fetchPlaybook,
  fetchShare,
  shareIdFromUrl,
} from "./api";
import { Contents, type Section } from "./components/Contents";
import { Diagram } from "./components/Diagram";
import { Effort } from "./components/Effort";
import { Library, NoPlaybook } from "./components/Library";
import { Overview } from "./components/Overview";
import { Playbook } from "./components/Playbook";
import { Questions } from "./components/Questions";
import { Verdicts } from "./components/Verdicts";
import { clearDraft, loadDraft, saveDraft } from "./draft";
import { VERDICT_LABEL } from "./labels";
import type {
  AnalyseResponse,
  Answer,
  EffortInput,
  Example,
  LibraryArticle,
  PlaybookResponse,
  SharedAnalysis,
} from "./types";

/** The server caps these too. Slicing here keeps a 422 off the screen. */
const MOST_ANSWERS = 12;

export default function App() {
  const [description, setDescription] = useState("");
  const [examples, setExamples] = useState<Example[]>([]);
  const [replayCase, setReplayCase] = useState<string | undefined>();
  const [result, setResult] = useState<AnalyseResponse | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  // Whole seconds since the current request started, and whether it carries
  // answers, so the wait can say what it is waiting for and for how long.
  const [elapsed, setElapsed] = useState(0);
  const [again, setAgain] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [exporting, setExporting] = useState(false);
  // "copied" or "downloaded", so the message can say what actually happened.
  const [handoff, setHandoff] = useState<"copied" | "downloaded" | null>(null);

  // The retrieved article, if the corpus covers this job. Kept apart from the
  // analysis because it arrives separately and outlives it failing.
  const [playbook, setPlaybook] = useState<PlaybookResponse | null>(null);
  // Every article there is, so an empty match can be explained rather than
  // looking like something broke. Empty if it fails to load, which hides both
  // places it appears and costs nothing else.
  const [library, setLibrary] = useState<LibraryArticle[]>([]);

  // Keyed by the question text, because ids are regenerated on every run and an
  // answer has to outlive the analysis that prompted it. Answers accumulate:
  // something said two rounds ago is still true now.
  const [answers, setAnswers] = useState<Record<string, string>>({});
  const [usedAnswers, setUsedAnswers] = useState<Answer[]>([]);

  const [effortInput, setEffortInput] = useState<EffortInput | null>(null);
  const [shared, setShared] = useState<SharedAnalysis | null>(null);
  const [shareUrl, setShareUrl] = useState<string | null>(null);
  const [shareExpiry, setShareExpiry] = useState<string | null>(null);
  const [sharing, setSharing] = useState(false);
  const [copied, setCopied] = useState(false);
  const [opening, setOpening] = useState(() => Boolean(shareIdFromUrl()));
  // Shown only when something was actually brought back, so the notice is news
  // rather than furniture.
  const [restored, setRestored] = useState(false);

  // Counted from the clock rather than by adding one a tick, because a
  // background tab slows its timers down and the count would drift.
  useEffect(() => {
    if (!busy) return;
    const started = Date.now();
    setElapsed(0);
    const timer = setInterval(
      () => setElapsed(Math.floor((Date.now() - started) / 1000)),
      1000,
    );
    return () => clearInterval(timer);
  }, [busy]);

  useEffect(() => {
    fetchExamples().then(setExamples).catch(() => setExamples([]));
    fetchLibrary().then(setLibrary).catch(() => setLibrary([]));
  }, []);

  // What was in the box last time, from this browser and nowhere else. Skipped
  // on a /s/<id> URL, where the visitor came to read somebody else's analysis
  // and has no interest in a draft of their own.
  useEffect(() => {
    if (shareIdFromUrl()) return;

    const draft = loadDraft();
    if (!draft) return;

    setDescription(draft.description);
    setAnswers(draft.answers);
    setRestored(true);
  }, []);

  // Written on a timer rather than on every keystroke. Storage is synchronous,
  // so writing a few kilobytes on each character typed is work done on the
  // thread that is trying to render the character.
  //
  // Checks the URL rather than the `shared` state, which is null until the fetch
  // comes back. In that gap the box is empty, an empty box clears the draft, and
  // opening somebody else's link would quietly delete your own work.
  useEffect(() => {
    if (shareIdFromUrl()) return;
    const timer = setTimeout(() => saveDraft(description, answers), 400);
    return () => clearTimeout(timer);
  }, [description, answers]);

  // A /s/<id> URL opens somebody else's analysis instead of a blank page.
  useEffect(() => {
    const id = shareIdFromUrl();
    if (!id) return;

    fetchShare(id)
      .then((analysis) => {
        setShared(analysis);
        setResult({
          ok: true,
          model: "a shared link",
          graph: analysis.graph,
          plan: analysis.plan,
          extraction_attempts: [],
          assessment_attempts: [],
          error: null,
        });
      })
      .catch((exc) => setError(exc instanceof Error ? exc.message : "Could not open that link."))
      .finally(() => setOpening(false));
  }, []);

  async function run(given: Answer[] = []) {
    setBusy(true);
    setAgain(given.length > 0);
    setError(null);
    setResult(null);
    setSelected(null);
    setShareUrl(null);
    setShared(null);
    setPlaybook(null);

    // Its own request, deliberately not awaited with the others. Retrieval takes
    // about a second and needs no generation, so the article lands while the
    // slow half is still working and stays on screen if that half never
    // finishes at all, which on a bad afternoon is the only thing that arrives.
    fetchPlaybook(description)
      .then(setPlaybook)
      .catch(() => setPlaybook(null));

    try {
      // Answering means going to the model. A recorded example replays one fixed
      // response, so replaying it would hand back the identical analysis and
      // look, reasonably enough, like the answers had been ignored.
      const response = await analyse(
        description,
        given.length ? undefined : replayCase,
        given,
      );
      setResult(response);
      setUsedAnswers(response.ok ? given : []);
      if (!response.ok && response.error) setError(response.error);
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "Something went wrong.");
    } finally {
      setBusy(false);
    }
  }

  /** Everything answered so far, trimmed and with the blanks dropped. */
  function answersGiven(): Answer[] {
    return Object.entries(answers)
      .map(([question, answer]) => ({ question, answer: answer.trim() }))
      .filter((a) => a.answer.length > 0)
      .slice(0, MOST_ANSWERS);
  }

  async function exportWorkflow() {
    if (!result?.graph) return;
    setExporting(true);
    try {
      const workflow = await buildWorkflow(result.graph, result.plan);

      // Straight to the clipboard, because n8n takes a paste onto its canvas
      // and that skips the file, the downloads folder and the import dialog.
      // If the browser refuses, fall back to the file rather than stopping.
      if (await copyToClipboard(workflow)) {
        setHandoff("copied");
      } else {
        // Some browsers refuse clipboard writes outright. Downloading instead
        // is the right fallback, but doing it silently leaves somebody staring
        // at a button that did nothing while a file lands in a folder they were
        // not looking at.
        const name = result.graph.title.toLowerCase().replace(/[^a-z0-9]+/g, "-");
        download(new Blob([workflow], { type: "application/json" }), `${name}.n8n.json`);
        setHandoff("downloaded");
      }
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "Could not build the workflow.");
    } finally {
      setExporting(false);
    }
  }

  async function downloadWorkflow() {
    if (!result?.graph) return;
    try {
      const workflow = await buildWorkflow(result.graph, result.plan);
      const name = result.graph.title.toLowerCase().replace(/[^a-z0-9]+/g, "-");
      download(new Blob([workflow], { type: "application/json" }), `${name}.n8n.json`);
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "Could not build the workflow.");
    }
  }

  async function makeShareLink() {
    if (!result?.graph) return;
    setSharing(true);
    try {
      const created = await createShare(result.graph, result.plan, effortInput);
      const url = `${window.location.origin}/s/${created.id}`;
      setShareUrl(url);
      setShareExpiry(created.expires_at);
      // Put it in the address bar too, so the obvious copy is the right one.
      window.history.replaceState(null, "", `/s/${created.id}`);
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "Could not create the link.");
    } finally {
      setSharing(false);
    }
  }

  async function copyLink() {
    if (!shareUrl) return;
    try {
      await navigator.clipboard.writeText(shareUrl);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      setCopied(false); // Clipboard access can be refused; the link is on screen anyway.
    }
  }

  function useExample(example: Example) {
    setDescription(example.description);
    setPlaybook(null);
    setRestored(false);
    setAnswers({}); // A different process, so nothing said about the last one holds.
    setUsedAnswers([]);
    // Recorded examples replay from disk, so they work with no API key and
    // cost nothing. Anything typed by hand goes to the model.
    setReplayCase(example.replayable ? example.id : undefined);
    setResult(null);
    setError(null);
    setShareUrl(null);
  }

  // Stable, or the effort panel's reporting effect loops.
  const handleEffort = useCallback((effort: EffortInput | null) => {
    setEffortInput(effort);
    setShareUrl(null); // The numbers changed, so the old link is out of date.
  }, []);

  const attempts = result
    ? [...result.extraction_attempts, ...result.assessment_attempts]
    : [];
  const repairs = attempts.filter((a) => !a.ok);

  // The article has one place on the page, near the top, and keeps it. It
  // arrives about a second after asking, long before the analysis, and moving
  // it once the rest turns up would make the reader find it twice.
  const article = playbook?.match ? (
    <Playbook match={playbook.match} retriever={playbook.retriever} />
  ) : playbook?.retriever && library.length > 0 ? (
    <NoPlaybook library={library} />
  ) : null;

  // After a failure only a real match is worth keeping on screen. "No article
  // for this job" under an error message is one more thing that went nowhere.
  const early = busy ? article : playbook?.match ? article : null;

  const given = answersGiven();

  const sections: Section[] = result?.graph
    ? [
        { id: "summary", label: "Summary" },
        { id: "steps", label: `Steps (${result.graph.steps.length})` },
        ...(result.graph.questions.length > 0
          ? [{ id: "questions", label: `Questions (${result.graph.questions.length})` }]
          : []),
        ...(result.plan ? [{ id: "time", label: "Time it takes" }] : []),
      ]
    : [];

  if (opening) {
    return (
      <div className="page">
        <p className="muted">Opening that link...</p>
      </div>
    );
  }

  return (
    <div className="page">
      <header className="masthead">
        <h1>Automation Architect</h1>
        <p>
          Work out what is safe to automate before anybody builds it. Describe
          something you do by hand, and every step comes back as one of three.
        </p>

        {/* The colours are taught here, once, so the diagram and the list can
            use them without explaining themselves every time. */}
        <ul className="key">
          <li className="key__item key__item--fully_automatable">
            <strong>{VERDICT_LABEL.fully_automatable}</strong>
            <span>A computer can do it with nobody watching.</span>
          </li>
          <li className="key__item key__item--automatable_with_control">
            <strong>{VERDICT_LABEL.automatable_with_control}</strong>
            <span>A computer can do it, once a person signs off or a limit applies.</span>
          </li>
          <li className="key__item key__item--human_required">
            <strong>{VERDICT_LABEL.human_required}</strong>
            <span>Judgement that should not be handed over.</span>
          </li>
        </ul>

        <p className="key__rule">
          A step that moves money, cannot be undone or carries legal weight never
          comes back as &ldquo;runs itself&rdquo;. That rule is in the code, so the
          model cannot argue its way past it.
        </p>
      </header>

      {shared ? (
        <section className="banner">
          <strong>You are looking at a shared analysis.</strong> It was created on{" "}
          {new Date(shared.created_at).toLocaleDateString("en-GB")} and the link stops
          working on {new Date(shared.expires_at).toLocaleDateString("en-GB")}.{" "}
          <a href="/">Analyse your own process instead.</a>
        </section>
      ) : (
        <section className="composer">
          <textarea
            value={description}
            placeholder={
              "Every morning I go through my emails looking for invoices. When I find " +
              "one I download the PDF, read the total off it, and type that into our " +
              "spreadsheet..."
            }
            onChange={(event) => {
              setDescription(event.target.value);
              setReplayCase(undefined);
            }}
            rows={6}
            spellCheck
          />

          <div className="composer__actions">
            <button onClick={() => run()} disabled={busy || description.trim().length < 20}>
              {busy ? "Working through it..." : "Analyse this"}
            </button>
            {replayCase && (
              <span className="composer__note">
                Recorded example. Runs without an API key.
              </span>
            )}
            {!replayCase && !busy && description.trim().length < 20 && (
              <span className="composer__note">
                A sentence or two is enough, or try an example below.
              </span>
            )}
            {restored && !replayCase && (
              <span className="composer__note">
                Picked up where you left off. Kept in this browser only.{" "}
                <button
                  className="linkish"
                  onClick={() => {
                    clearDraft();
                    setDescription("");
                    setAnswers({});
                    setRestored(false);
                  }}
                >
                  Clear it
                </button>
              </span>
            )}
          </div>

          {examples.length > 0 && (
            <div className="examples">
              <span>Or try one:</span>
              {examples.map((example) => (
                <button
                  key={example.id}
                  className="examples__chip"
                  onClick={() => useExample(example)}
                >
                  {example.label}
                </button>
              ))}
            </div>
          )}

          {library.length > 0 && (
            <details className="library">
              <summary>Articles it can match you to ({library.length})</summary>
              <p className="library__lead">
                A small written library, searched by meaning against what you type.
                When one is close enough, it appears beside your process. They are
                shown to you, not fed to the model that judges your process.
              </p>
              <Library articles={library} />
            </details>
          )}
        </section>
      )}

      {error && <div className="error">{error}</div>}

      {/* Before the analysis lands: the wait, and the article beside it in the
          place it will stay once the rest arrives. */}
      {!result?.graph && (busy || early) && (
        <div className="overview">
          {/* Ten to twenty seconds is a long time to look at a button that says
              it is busy. This says what is happening, how long it usually takes
              and how long it has been, which is most of what makes a wait
              bearable. */}
          {busy && (
            <section className="card working" role="status">
              <div className="working__bar" aria-hidden="true">
                <span />
              </div>
              <p className="working__title">
                {again ? "Working it out again with your answers" : "Working through your process"}
                {elapsed > 0 && (
                  <span className="working__clock" aria-hidden="true">
                    {elapsed}s
                  </span>
                )}
              </p>
              <p className="working__note">
                {elapsed < 30
                  ? "It maps the steps, judges each one, then our checks go over every verdict. Usually 10 to 20 seconds."
                  : "Taking longer than usual. If it runs out of time it stops and says so, rather than leaving you waiting."}
              </p>
            </section>
          )}
          {early}
        </div>
      )}

      {repairs.length > 0 && (
        <details className="repairs">
          <summary>
            The checker rejected {repairs.length}{" "}
            {repairs.length === 1 ? "answer" : "answers"} and asked again
          </summary>
          {repairs.map((attempt, index) => (
            <div key={index}>
              <strong>Attempt {attempt.number}</strong>
              <ul>
                {attempt.errors.map((message, i) => (
                  <li key={i}>{message}</li>
                ))}
              </ul>
            </div>
          ))}
        </details>
      )}

      {/* Overview first, then the detail on demand: the answer and the article
          at the top, every step and its reasoning below them, and what to do
          next after that. The bar keeps all of it one click away. */}
      {result?.graph && (
        <main className="report">
          <header className="report__head">
            <div className="results__head">
              <h2>{result.graph.title}</h2>
              <div className="results__buttons">
                {!shared && (
                  <button
                    className="secondary"
                    onClick={makeShareLink}
                    disabled={sharing}
                    title="A link anyone can open. Expires after 30 days."
                  >
                    {sharing ? "Creating..." : "Share"}
                  </button>
                )}
                <button
                  className="secondary"
                  onClick={exportWorkflow}
                  disabled={exporting}
                  title="Copies the workflow. Paste it onto an n8n canvas."
                >
                  {exporting ? "Building..." : handoff === "copied" ? "Copied" : "Copy for n8n"}
                </button>
              </div>
            </div>

            <p className="results__summary">{result.graph.summary}</p>

            {usedAnswers.length > 0 && (
              <div className="answered">
                <strong>Worked out again using what you told it:</strong>
                <ul>
                  {usedAnswers.map((answer) => (
                    <li key={answer.question}>
                      <span className="answered__question">{answer.question}</span>
                      {answer.answer}
                    </li>
                  ))}
                </ul>
              </div>
            )}

            {handoff && (
              <p className="handoff">
                {handoff === "copied" ? (
                  <>
                    On your clipboard. Open n8n, click the empty canvas and press{" "}
                    <kbd>Ctrl</kbd>+<kbd>V</kbd>.{" "}
                  </>
                ) : (
                  <>
                    Your browser would not let the page use the clipboard, so the
                    workflow downloaded instead. In n8n, use Import from File.{" "}
                  </>
                )}
                Steps where you named the system arrive as real nodes and still
                need their credentials. The rest are placeholders saying what
                belongs there.{" "}
                {handoff === "copied" && (
                  <button className="linkish" onClick={downloadWorkflow}>
                    Download the file instead
                  </button>
                )}
              </p>
            )}

            {shareUrl && (
              <div className="sharebox">
                <div className="sharebox__row">
                  <input readOnly value={shareUrl} onFocus={(e) => e.target.select()} />
                  <button onClick={copyLink}>{copied ? "Copied" : "Copy"}</button>
                </div>
                <p className="sharebox__note">
                  Anyone with this link can read the analysis, so treat it as public.
                  Do not share one containing customer names or anything confidential.
                  {shareExpiry && (
                    <> It stops working on {new Date(shareExpiry).toLocaleDateString("en-GB")}.</>
                  )}
                </p>
              </div>
            )}
          </header>

          <Contents sections={sections} />

          <div className="overview" id="summary">
            {result.plan ? (
              <Overview graph={result.graph} plan={result.plan} onPick={setSelected} />
            ) : (
              <section className="card answer">
                <span className="card__label">The answer</span>
                <p className="muted">
                  The process was mapped, but the judgement stage did not complete.
                </p>
              </section>
            )}
            {article}
          </div>

          <section className="steps" id="steps">
            <header className="section__head">
              <h2>Step by step</h2>
              <p>
                Anything that needs you comes first. Click a step in the diagram to
                find out why it got its verdict.
              </p>
            </header>

            <div className="results">
              <div className="results__diagram">
                <Diagram
                  graph={result.graph}
                  plan={result.plan}
                  selected={selected}
                  onSelect={setSelected}
                />
              </div>

              <div className="results__panel">
                {result.plan && (
                  <Verdicts
                    graph={result.graph}
                    plan={result.plan}
                    selected={selected}
                    onSelect={setSelected}
                  />
                )}
              </div>
            </div>
          </section>

          <div className="next">
            {result.graph.questions.length > 0 && (
              <Questions
                questions={result.graph.questions}
                readOnly={Boolean(shared)}
                answers={answers}
                onAnswer={(question, answer) =>
                  setAnswers((current) => ({ ...current, [question]: answer }))
                }
                given={given.length}
                busy={busy}
                onRun={() => run(given)}
              />
            )}

            {result.plan && (
              <section className="card time" id="time">
                <h2>Time it takes</h2>
                <Effort
                  graph={result.graph}
                  plan={result.plan}
                  initial={shared?.effort ?? null}
                  onChange={handleEffort}
                />
              </section>
            )}
          </div>
        </main>
      )}

      <footer className="footer">
        {/* "none" is the placeholder on a response that never reached a model,
            and "Answered by none" is a sentence no reader should be shown. */}
        {result && result.model !== "none" && (
          <span>{answeredBy(result.model)} </span>
        )}
        <a href="https://github.com/latczar/automation-architect">Source on GitHub</a>
      </footer>
    </div>
  );
}

/**
 * Who produced the analysis, in words a reader would use.
 *
 * The server names its clients for developers: "recording(gemini:gemini-3.5-
 * flash-lite)" means the local wrapper that saves each response, around the
 * Gemini client, around a model id. A reader wants "Gemini 3.5 Flash-Lite".
 */
function answeredBy(model: string): string {
  if (model === "a shared link") return "Opened from a shared link.";

  const inner = model.replace(/^recording\((.*)\)$/, "$1");
  if (inner.startsWith("replay:")) return "Answered by a recorded example, not a live model.";

  if (inner.startsWith("gemini:")) {
    const name = inner
      .slice("gemini:".length)
      .split("-")
      .map((word) => word.charAt(0).toUpperCase() + word.slice(1))
      .join(" ")
      .replace(" Flash Lite", " Flash-Lite");
    return `Answered by ${name}.`;
  }

  return `Answered by ${inner}.`;
}
