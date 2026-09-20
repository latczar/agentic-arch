import { useCallback, useEffect, useState } from "react";

import {
  analyse,
  createShare,
  download,
  exportN8n,
  fetchExamples,
  fetchShare,
  shareIdFromUrl,
} from "./api";
import { Diagram } from "./components/Diagram";
import { Effort } from "./components/Effort";
import { Verdicts } from "./components/Verdicts";
import type {
  AnalyseResponse,
  EffortInput,
  Example,
  SharedAnalysis,
} from "./types";

export default function App() {
  const [description, setDescription] = useState("");
  const [examples, setExamples] = useState<Example[]>([]);
  const [replayCase, setReplayCase] = useState<string | undefined>();
  const [result, setResult] = useState<AnalyseResponse | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [exporting, setExporting] = useState(false);

  const [effortInput, setEffortInput] = useState<EffortInput | null>(null);
  const [shared, setShared] = useState<SharedAnalysis | null>(null);
  const [shareUrl, setShareUrl] = useState<string | null>(null);
  const [shareExpiry, setShareExpiry] = useState<string | null>(null);
  const [sharing, setSharing] = useState(false);
  const [copied, setCopied] = useState(false);
  const [opening, setOpening] = useState(() => Boolean(shareIdFromUrl()));

  useEffect(() => {
    fetchExamples().then(setExamples).catch(() => setExamples([]));
  }, []);

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

  async function run() {
    setBusy(true);
    setError(null);
    setResult(null);
    setSelected(null);
    setShareUrl(null);
    setShared(null);
    try {
      const response = await analyse(description, replayCase);
      setResult(response);
      if (!response.ok && response.error) setError(response.error);
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "Something went wrong.");
    } finally {
      setBusy(false);
    }
  }

  async function exportWorkflow() {
    if (!result?.graph) return;
    setExporting(true);
    try {
      const blob = await exportN8n(result.graph, result.plan);
      const name = result.graph.title.toLowerCase().replace(/[^a-z0-9]+/g, "-");
      download(blob, `${name}.n8n.json`);
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "Could not build the export.");
    } finally {
      setExporting(false);
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
        <h1>AI Automation Architect</h1>
        <p>
          Describe something repetitive you do by hand. See which parts a computer
          could take over, and where it should stop and ask you first.
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
            <button onClick={run} disabled={busy || description.trim().length < 20}>
              {busy ? "Working through it..." : "Analyse this"}
            </button>
            {replayCase && (
              <span className="composer__note">
                Recorded example. Runs without an API key.
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
        </section>
      )}

      {error && <div className="error">{error}</div>}

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

      {result?.graph && (
        <main className="results">
          <div className="results__diagram">
            <div className="results__head">
              <div>
                <h2>{result.graph.title}</h2>
                <p className="results__summary">{result.graph.summary}</p>
              </div>
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
                  title="An importable n8n workflow. Integration nodes are placeholders."
                >
                  {exporting ? "Building..." : "Export to n8n"}
                </button>
              </div>
            </div>

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

            <Diagram
              graph={result.graph}
              plan={result.plan}
              selected={selected}
              onSelect={setSelected}
            />
          </div>

          <aside className="results__panel">
            {result.plan ? (
              <Verdicts
                graph={result.graph}
                plan={result.plan}
                selected={selected}
                onSelect={setSelected}
              />
            ) : (
              <p className="muted">
                The process was mapped, but the judgement stage did not complete.
              </p>
            )}

            {result.plan && (
              <Effort
                graph={result.graph}
                plan={result.plan}
                initial={shared?.effort ?? null}
                onChange={handleEffort}
              />
            )}

            {result.graph.questions.length > 0 && (
              <section className="questions">
                <h3>Worth asking before building anything</h3>
                {result.graph.questions.map((question) => (
                  <div key={question.id} className="question">
                    <p className="question__text">{question.question}</p>
                    <p className="question__why">{question.why_it_matters}</p>
                    {question.suggested_answers.length > 0 && (
                      <ul className="question__answers">
                        {question.suggested_answers.map((answer, i) => (
                          <li key={i}>{answer}</li>
                        ))}
                      </ul>
                    )}
                  </div>
                ))}
              </section>
            )}
          </aside>
        </main>
      )}

      <footer className="footer">
        {result && <span>Answered by {result.model}. </span>}
        <a href="https://github.com/latczar/agentic-arch">Source on GitHub</a>
      </footer>
    </div>
  );
}
