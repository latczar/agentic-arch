import { useEffect, useMemo, useState } from "react";

import { calculateEffort } from "../api";
import type {
  AutomationPlan,
  EffortInput,
  EffortSummary,
  Period,
  ProcessGraph,
} from "../types";

const PERIODS: { value: Period; label: string }[] = [
  { value: "day", label: "day" },
  { value: "working_day", label: "working day" },
  { value: "week", label: "week" },
  { value: "month", label: "month" },
];

const DEFAULT_MINUTES = 2;

/** Hours are the wrong unit below one. Nobody says "0.17 hours". */
function duration(hours: number): string {
  if (hours <= 0) return "none";
  if (hours < 1) return `${Math.round(hours * 60)} minutes`;
  return `${hours % 1 === 0 ? hours : hours.toFixed(1)} hours`;
}

interface Props {
  graph: ProcessGraph;
  plan: AutomationPlan;
}

export function Effort({ graph, plan }: Props) {
  const [open, setOpen] = useState(false);
  const [times, setTimes] = useState(5);
  const [period, setPeriod] = useState<Period>("week");
  const [minutes, setMinutes] = useState<Record<string, number>>(() =>
    Object.fromEntries(graph.steps.map((step) => [step.id, DEFAULT_MINUTES])),
  );
  const [summary, setSummary] = useState<EffortSummary | null>(null);
  const [error, setError] = useState<string | null>(null);

  const input: EffortInput = useMemo(
    () => ({ times_per_period: times, period, minutes_per_step: minutes }),
    [times, period, minutes],
  );

  useEffect(() => {
    if (!open || times <= 0) return;

    // Debounced: the arithmetic lives on the server so there is one tested copy
    // of it, which means a request per keystroke without this.
    const timer = setTimeout(() => {
      calculateEffort(plan, input)
        .then((result) => {
          setSummary(result);
          setError(null);
        })
        .catch((exc) => setError(exc instanceof Error ? exc.message : "Could not work that out."));
    }, 300);

    return () => clearTimeout(timer);
  }, [open, plan, input, times]);

  if (!open) {
    return (
      <section className="effort">
        <button className="secondary" onClick={() => setOpen(true)}>
          Work out how much time this takes
        </button>
        <p className="effort__pitch">
          Two questions, and the arithmetic is yours rather than ours. We will not
          guess these numbers for you.
        </p>
      </section>
    );
  }

  const nameOf = (id: string) => graph.steps.find((s) => s.id === id)?.name ?? id;

  return (
    <section className="effort effort--open">
      <h3>How much time this takes</h3>

      <p className="effort__frequency">
        I do this{" "}
        <input
          type="number"
          min={1}
          max={1000}
          value={times}
          onChange={(event) => setTimes(Math.max(1, Number(event.target.value) || 1))}
          aria-label="How many times"
        />{" "}
        times a{" "}
        <select
          value={period}
          onChange={(event) => setPeriod(event.target.value as Period)}
          aria-label="Per period"
        >
          {PERIODS.map((p) => (
            <option key={p.value} value={p.value}>
              {p.label}
            </option>
          ))}
        </select>
      </p>

      <table className="effort__steps">
        <thead>
          <tr>
            <th>Step</th>
            <th>Minutes each time</th>
          </tr>
        </thead>
        <tbody>
          {graph.steps.map((step) => (
            <tr key={step.id}>
              <td>{step.name}</td>
              <td>
                <input
                  type="number"
                  min={0}
                  max={480}
                  value={minutes[step.id] ?? 0}
                  onChange={(event) =>
                    setMinutes((current) => ({
                      ...current,
                      [step.id]: Math.max(0, Number(event.target.value) || 0),
                    }))
                  }
                  aria-label={`Minutes for ${step.name}`}
                />
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      {error && <p className="effort__error">{error}</p>}

      {summary && (
        <div className="effort__result">
          <p className="effort__headline">
            That is <strong>{duration(summary.hours_per_month)}</strong> a month, of
            which <strong>{duration(summary.hours_that_could_run_without_you)}</strong>{" "}
            could run without you.
          </p>

          <div className="effort__bar" aria-hidden>
            <span
              className="effort__bar-fill"
              style={{ width: `${summary.percentage_automatable}%` }}
            />
          </div>
          <p className="effort__percent">
            {summary.percentage_automatable}% of the time, across roughly{" "}
            {summary.runs_per_month} runs a month
          </p>

          <ul className="effort__breakdown">
            {summary.steps
              .filter((s) => s.hours_per_month > 0)
              .sort((a, b) => b.hours_per_month - a.hours_per_month)
              .map((step) => (
                <li key={step.step_id} data-saved={step.could_run_without_you}>
                  <span>{nameOf(step.step_id)}</span>
                  <span>{duration(step.hours_per_month)}</span>
                </li>
              ))}
          </ul>

          <p className="effort__caveat">{summary.caveat}</p>
        </div>
      )}
    </section>
  );
}
