import type { LibraryArticle } from "../types";
import { render } from "./Playbook";

/**
 * The written articles, listed so it is plain what the page can match.
 *
 * Without this an empty article panel looks like the page failing. With it,
 * "no article for this job" is something a person can check for themselves.
 *
 * The wording is deliberate. These are not what the model knows: the model that
 * judges a process never reads them. They are a small library searched against
 * what somebody typed, and the closest one is shown to the person.
 */
export function Library({ articles }: { articles: LibraryArticle[] }) {
  return (
    <ul className="library__list">
      {articles.map((article) => (
        <li key={article.id}>
          <details>
            <summary>{article.title}</summary>
            {article.also_called.length > 0 && (
              <p className="library__aliases">
                Also called: {article.also_called.join(", ")}
              </p>
            )}
            <div className="playbook__body">{render(article.body)}</div>
          </details>
        </li>
      ))}
    </ul>
  );
}
