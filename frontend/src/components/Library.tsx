import type { LibraryArticle } from "../types";
import { render } from "./Playbook";

/**
 * The article slot when the search ran and found nothing close enough.
 *
 * Only shown when the search actually ran. A failed search has no retriever
 * name, and claiming the library has no article for this job would then be a
 * guess dressed up as a fact.
 */
export function NoPlaybook({ library }: { library: LibraryArticle[] }) {
  return (
    <section className="playbook playbook--none">
      <header className="playbook__head">
        <span className="playbook__label">How this job usually goes</span>
        <h3>No article for this job yet</h3>
      </header>
      <p className="playbook__caveat">
        Nothing in the library was close enough to be worth showing.
      </p>
      {/* Closed, because a card saying "nothing here" should not then take up
          more room than the cards that found something. */}
      <details className="library library--inline">
        <summary>See the {library.length} jobs it covers</summary>
        <Library articles={library} />
      </details>
    </section>
  );
}

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
