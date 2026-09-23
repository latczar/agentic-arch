import type { ReactElement } from "react";

import type { PlaybookMatch } from "../types";

/**
 * The article for a job, shown beside somebody's own process and never instead
 * of it. What they described is what they do. This is only how the job tends to
 * go elsewhere, which is worth reading precisely where the two differ.
 */
interface Props {
  match: PlaybookMatch;
  retriever: string;
}

/**
 * Enough Markdown to render the corpus, and no library.
 *
 * The articles are written to a fixed shape: headings, numbered steps, bullets
 * and bold labels. A parser for that is thirty lines, where a dependency is
 * around 40KB of someone else's code plus whatever it does with raw HTML. If
 * the corpus ever needs tables or links this stops being the right call.
 * Shared with the library list, which shows the same articles.
 */
export function render(body: string) {
  const blocks: ReactElement[] = [];
  let list: string[] = [];
  let ordered = false;

  const flush = () => {
    if (!list.length) return;
    const items = list.map((item, i) => <li key={i}>{bold(item)}</li>);
    blocks.push(
      ordered ? <ol key={blocks.length}>{items}</ol> : <ul key={blocks.length}>{items}</ul>,
    );
    list = [];
  };

  for (const raw of body.split("\n")) {
    const line = raw.trim();

    if (!line) {
      flush();
      continue;
    }
    if (line.startsWith("#")) {
      flush();
      // Every article opens with a level one heading repeating its own title,
      // which the panel header already shows. Deeper headings are the sections.
      if (!line.startsWith("##")) continue;
      blocks.push(<h4 key={blocks.length}>{line.replace(/^#+\s*/, "")}</h4>);
      continue;
    }
    if (/^\d+\.\s/.test(line)) {
      if (!ordered) flush();
      ordered = true;
      list.push(line.replace(/^\d+\.\s*/, ""));
      continue;
    }
    if (line.startsWith("- ")) {
      if (ordered) flush();
      ordered = false;
      list.push(line.slice(2));
      continue;
    }

    flush();
    blocks.push(<p key={blocks.length}>{bold(line)}</p>);
  }

  flush();
  return blocks;
}

/** **Label.** at the start of a line is how every article emphasises a step. */
function bold(text: string) {
  return text.split(/(\*\*[^*]+\*\*)/g).map((part, i) =>
    part.startsWith("**") && part.endsWith("**") ? (
      <strong key={i}>{part.slice(2, -2)}</strong>
    ) : (
      <span key={i}>{part}</span>
    ),
  );
}

export function Playbook({ match, retriever }: Props) {
  return (
    <section className="playbook">
      <header className="playbook__head">
        <span className="playbook__label">How this job usually goes</span>
        <h3>{match.title}</h3>
      </header>

      <p className="playbook__caveat">
        Retrieved from a written library, not generated. It describes the job in
        general, so the useful part is wherever it differs from yours.
      </p>

      <details>
        <summary>Read the article</summary>
        <div className="playbook__body">{render(match.body)}</div>
      </details>

      <p className="playbook__provenance">
        Matched by {retriever === "embeddings" ? "meaning" : "wording"} at{" "}
        {match.score.toFixed(2)}.
      </p>
    </section>
  );
}
