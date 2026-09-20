import { useEffect, useState } from "react";

import { analyse, fetchExamples } from "./api";
import { Diagram } from "./components/Diagram";
import { Verdicts } from "./components/Verdicts";
import type { AnalyseResponse, Example } from "./types";

export default function App() {
  const [description, setDescription] = useState("");
  const [examples, setExamples] = useState<Example[]>([]);
  const [replayCase, setReplayCase] = useState<string | undefined>();
  const [result, setResult] = useState<AnalyseResponse | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetchExamples().then(setExamples).catch(() => setExamples([]));
  }, []);

  async function run() {
    setBusy(true);
    setError(null);
    setResult(null);
    setSelected(null);
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

  function useExample(example: Example) {
    setDescription(example.description);
    // Recorded examples replay from disk, so they work with no API key and
    // cost nothing. Anything typed by hand goes to the model.
    setReplayCase(example.replayable ? example.id : undefined);
    setResult(null);
    setError(null);
  }

  const attempts = result
    ? [...result.extraction_attempts, ...result.assessment_attempts]
    : [];
  const repairs = attempts.filter((a) => !a.ok);

  return (
    <div className="page">
      <header className="masthead">
        <h1>AI Automation Architect</h1>
        <p>
          Describe something repetitive you do by hand. See which parts a computer
          could take over, and where it should stop and ask you first.
        </p>
      </header>

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
            <h2>{result.graph.title}</h2>
            <p className="results__summary">{result.graph.summary}</p>
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
