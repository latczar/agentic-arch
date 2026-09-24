import type { Question } from "../types";

interface Props {
  questions: Question[];
  /** A shared analysis is read, not answered, so it gets no controls. */
  readOnly: boolean;
  answers: Record<string, string>;
  onAnswer: (question: string, answer: string) => void;
  /** How many answers are ready to send. */
  given: number;
  busy: boolean;
  onRun: () => void;
}

/**
 * The questions it could not answer from the description, and a way to answer
 * them.
 *
 * Its own section rather than the foot of the verdict list, because it is the
 * one part of the result that asks the reader to do something, and a request
 * nobody scrolls far enough to see is not much of a request.
 */
export function Questions({ questions, readOnly, answers, onAnswer, given, busy, onRun }: Props) {
  return (
    <section className="card questions" id="questions">
      <h2>Before you build</h2>
      {!readOnly && (
        <p className="questions__lead">
          Answer any of these and it will work the process out again, treating what
          you say as fact rather than as a suggestion.
        </p>
      )}

      {questions.map((question) => (
        <div key={question.id} className="question">
          {/* Said out loud, like the overrides. A question the model never asked
              should not pass itself off as one it did. */}
          {question.added_by_us && (
            <span className="question__ours">Asked by our checks, not the model</span>
          )}
          <p className="question__text">{question.question}</p>
          <p className="question__why">{question.why_it_matters}</p>

          {question.suggested_answers.length > 0 && (
            <div className="question__answers">
              {question.suggested_answers.map((answer, i) =>
                readOnly ? (
                  <span key={i} className="question__suggestion">
                    {answer}
                  </span>
                ) : (
                  <button
                    key={i}
                    type="button"
                    className={`question__suggestion question__suggestion--pick ${
                      answers[question.question] === answer ? "question__suggestion--chosen" : ""
                    }`}
                    aria-pressed={answers[question.question] === answer}
                    onClick={() => onAnswer(question.question, answer)}
                  >
                    {answer}
                  </button>
                ),
              )}
            </div>
          )}

          {!readOnly && (
            <input
              className="question__input"
              value={answers[question.question] ?? ""}
              placeholder="Or answer in your own words"
              aria-label={`Your answer: ${question.question}`}
              onChange={(event) => onAnswer(question.question, event.target.value)}
            />
          )}
        </div>
      ))}

      {!readOnly && given > 0 && (
        <button className="questions__again" onClick={onRun} disabled={busy}>
          {busy
            ? "Working through it again..."
            : `Analyse again with ${given} ${given === 1 ? "answer" : "answers"}`}
        </button>
      )}
    </section>
  );
}
