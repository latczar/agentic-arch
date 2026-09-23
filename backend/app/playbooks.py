"""A small library of how common back-office jobs are usually shaped, and two
ways of finding the right one from a description somebody typed.

The point is not to replace what the person said. It is to put a known-good
shape next to it, because the commonest failure in this kind of conversation is
not a wrong process map, it is a process map that quietly leaves out the step
nobody mentioned. Somebody describing their rent run rarely thinks to say what
happens when a payment is short, and the playbook says it out loud.

Two retrievers live here, behind one interface, because which one is better is
a question with an answer rather than a matter of taste. Twelve documents is
small enough that ordinary word matching may well beat embeddings, and a corpus
this size does not justify a vector database, a service, or a bill. Both are
scored against the same labelled set by scripts/eval.py, and the numbers are in
the README.

Neither retriever is allowed to return its best guess regardless. Below its
threshold it returns nothing, which is the same rule as everywhere else here: a
confident wrong answer costs more than no answer.
"""

from __future__ import annotations

import json
import math
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

PLAYBOOK_DIR = Path(__file__).resolve().parent.parent / "playbooks"
VECTOR_FILE = PLAYBOOK_DIR / "vectors.json"

# Words carrying no signal about which job is being described. Deliberately
# short: cutting too much is how "check" and "pay" go missing, and those are
# exactly the words that tell two playbooks apart.
STOPWORDS = frozenset(
    """
    a an the and or but if then than that this these those of in on at to for
    with from by as is are was were be been being do does did doing have has
    had having it its i we you they he she them us our your their my me
    so just also very much more most some any each every all both
    about into over under out up down off again once here there when where
    how what which who whom why can could would should will shall may might
    must need needs am no not only own same too s t
    """.split()
)

TOKEN = re.compile(r"[a-z0-9]+")


def _tokenise(text: str) -> list[str]:
    """Lower case words, stopwords dropped, crude plurals folded together.

    The singularisation is two rules and no dictionary. "invoices" and "invoice"
    have to match or half the corpus is unreachable, and anything cleverer would
    need a dependency to fix a problem this does not have.
    """

    words = []
    for raw in TOKEN.findall(text.lower()):
        if raw in STOPWORDS or len(raw) < 2:
            continue
        if len(raw) > 3 and raw.endswith("ies"):
            raw = raw[:-3] + "y"
        elif len(raw) > 3 and raw.endswith("s") and not raw.endswith("ss"):
            raw = raw[:-1]
        words.append(raw)
    return words


@dataclass(frozen=True)
class Playbook:
    """One article: how a job is usually shaped and where it needs a person."""

    id: str
    title: str
    also_called: tuple[str, ...]
    body: str

    @property
    def searchable(self) -> str:
        """What gets matched against.

        The aliases are repeated because they carry most of the signal. Somebody
        types "chasing tenants", not "rent collection and arrears chase", and in
        a document of four hundred words one mention of the words they actually
        used is easily drowned.
        """

        aliases = ", ".join(self.also_called)
        return f"{self.title}. {aliases}. {aliases}. {self.body}"

    @property
    def document(self) -> str:
        """The same article without the alias repetition, for embedding.

        Repeating aliases helps a word matcher and does nothing useful for a
        sentence encoder, which reads them as a list either way. Keeping the two
        texts separate means the comparison between the retrievers measures the
        retrievers rather than a trick applied to one of them.
        """

        return f"{self.title}. Also called: {', '.join(self.also_called)}.\n\n{self.body}"


def _parse(text: str, fallback_id: str) -> Playbook:
    """Read the frontmatter block, or treat the whole file as body if absent."""

    meta: dict[str, str] = {}
    body = text

    if text.startswith("---"):
        _, raw, body = text.split("---", 2)
        for line in raw.strip().splitlines():
            if ":" in line:
                key, value = line.split(":", 1)
                meta[key.strip()] = value.strip()

    aliases = tuple(
        part.strip() for part in meta.get("also_called", "").split(",") if part.strip()
    )
    return Playbook(
        id=meta.get("id", fallback_id),
        title=meta.get("title", fallback_id.replace("-", " ").capitalize()),
        also_called=aliases,
        body=body.strip(),
    )


def load_playbooks(directory: Path = PLAYBOOK_DIR) -> tuple[Playbook, ...]:
    """Every playbook on disk, in a stable order so scores are reproducible."""

    if not directory.is_dir():
        return ()
    return tuple(
        _parse(path.read_text(encoding="utf-8"), path.stem)
        for path in sorted(directory.glob("*.md"))
    )


@dataclass(frozen=True)
class Match:
    playbook: Playbook
    score: float


class Retriever(Protocol):
    name: str

    def search(self, query: str, limit: int = 3) -> list[Match]:
        """Best matches above this retriever's threshold, strongest first."""


# --- Word matching -----------------------------------------------------------

# Okapi BM25's usual constants. k1 decides how fast repeating a word stops
# helping, b how hard a long document is penalised for being long. Left at the
# values everybody uses, because tuning them on twelve documents would be
# fitting the corpus rather than improving the method.
K1 = 1.5
B = 0.75

# Swept across the labelled set in evals/playbook_cases.py rather than guessed,
# and deliberately not the highest scoring value.
#
#     threshold   right    quiet   total   irrelevant articles offered
#         3.25    13/14     6/8    19/22   2
#         4.00    10/14     8/8    18/22   0
#
# 3.25 wins on the total and loses on the thing that matters. An article shown
# beside somebody's process is a claim that this is the job they are describing,
# so offering the payroll article to somebody automating parking permits makes
# the whole page look like it is guessing. Showing nothing costs them nothing.
#
# One point of total, traded for never being confidently wrong.
LEXICAL_THRESHOLD = 4.0


class LexicalRetriever:
    """BM25 over the corpus. No API, no vectors, no network, no cold start.

    Worth trying properly rather than as a straw man. At this size the
    vocabulary is distinctive, and "rent" appearing in one document out of
    twelve is an extremely strong signal that embeddings have to work to beat.
    """

    name = "bm25"

    def __init__(
        self,
        playbooks: tuple[Playbook, ...] | None = None,
        threshold: float = LEXICAL_THRESHOLD,
    ) -> None:
        self.playbooks = playbooks if playbooks is not None else load_playbooks()
        self.threshold = threshold

        self._docs = [Counter(_tokenise(p.searchable)) for p in self.playbooks]
        self._lengths = [sum(d.values()) for d in self._docs]
        self._average = (sum(self._lengths) / len(self._lengths)) if self._docs else 0.0

        # How many documents each word appears in, for the rarity weighting.
        self._seen_in: Counter[str] = Counter()
        for doc in self._docs:
            self._seen_in.update(doc.keys())

    def _idf(self, word: str) -> float:
        total = len(self._docs)
        seen = self._seen_in.get(word, 0)
        return math.log(1 + (total - seen + 0.5) / (seen + 0.5))

    def score(self, query: str, index: int) -> float:
        doc = self._docs[index]
        length = self._lengths[index]
        total = 0.0

        for word in _tokenise(query):
            count = doc.get(word, 0)
            if not count:
                continue
            norm = count + K1 * (1 - B + B * length / (self._average or 1))
            total += self._idf(word) * count * (K1 + 1) / norm

        return total

    def search(self, query: str, limit: int = 3) -> list[Match]:
        scored = [
            Match(playbook, self.score(query, index))
            for index, playbook in enumerate(self.playbooks)
        ]
        above = [m for m in scored if m.score >= self.threshold]
        above.sort(key=lambda m: (-m.score, m.playbook.id))
        return above[:limit]


# --- Embeddings --------------------------------------------------------------

# Swept the same way as the lexical one, and settled the same way. 0.61 and 0.63
# both total 21/22; 0.63 is the one that never offers an irrelevant article.
#
#     threshold   right    quiet   total   irrelevant articles offered
#         0.61    14/14     7/8    21/22   1
#         0.63    13/14     8/8    21/22   0
EMBEDDING_THRESHOLD = 0.63


def cosine(a: list[float], b: list[float]) -> float:
    """Dot product of two vectors, normalising in case they are not unit length."""

    dot = sum(x * y for x, y in zip(a, b))
    size_a = math.sqrt(sum(x * x for x in a))
    size_b = math.sqrt(sum(y * y for y in b))
    if not size_a or not size_b:
        return 0.0
    return dot / (size_a * size_b)


class EmbeddingRetriever:
    """Similarity against vectors computed once and committed to the repo.

    There is no vector database and there should not be. Twelve documents of a
    few hundred dimensions is a file, and the search is a loop. Adding a service
    here would be infrastructure to run, pay for and explain, in exchange for
    making a sub-millisecond loop faster.

    The query still needs embedding at request time, which is one API call. That
    endpoint answers in about a second even when generation is congested, which
    is why this is usable on days when the rest of the pipeline is not.
    """

    name = "embeddings"

    def __init__(
        self,
        embed_query,
        playbooks: tuple[Playbook, ...] | None = None,
        vectors: dict[str, list[float]] | None = None,
        threshold: float = EMBEDDING_THRESHOLD,
    ) -> None:
        self.playbooks = playbooks if playbooks is not None else load_playbooks()
        self.vectors = vectors if vectors is not None else load_vectors()
        self.threshold = threshold
        self._embed_query = embed_query

    def search(self, query: str, limit: int = 3) -> list[Match]:
        if not self.vectors:
            return []

        asked = self._embed_query(query)
        scored = []
        for playbook in self.playbooks:
            stored = self.vectors.get(playbook.id)
            if stored:
                scored.append(Match(playbook, cosine(asked, stored)))

        above = [m for m in scored if m.score >= self.threshold]
        above.sort(key=lambda m: (-m.score, m.playbook.id))
        return above[:limit]


# --- Choosing between them ---------------------------------------------------


class FallbackRetriever:
    """The better retriever, with the one that always works underneath it.

    Measured over the same labelled set, at thresholds tuned the same way:

        bm25          10/14 right   8/8 quiet   18/22
        embeddings    13/14 right   8/8 quiet   21/22

    Embeddings win, and they win exactly where the theory says they should. The
    query "we sort out the rent money each month" shares almost no vocabulary
    with the rent article and BM25 hands back the tenancy renewal one instead,
    confidently, because renewals talk about rent constantly. That is the case
    word matching cannot reach however it is tuned.

    Worth writing down that this was not the expected result. Twelve documents
    with distinctive vocabulary is exactly where lexical search is supposed to
    hold its own, and the reason to measure rather than assume.

    So embeddings lead, and BM25 is the fallback rather than the loser: it needs
    no key, no network and no build step, which means the panel still appears on
    a clone with no API key and on an afternoon when the endpoint is unwell.
    """

    name = "embeddings-then-bm25"

    def __init__(self, embed_query, playbooks: tuple[Playbook, ...] | None = None) -> None:
        self.playbooks = playbooks if playbooks is not None else load_playbooks()
        self.lexical = LexicalRetriever(self.playbooks)
        self.embedding = EmbeddingRetriever(embed_query, self.playbooks)
        # What actually answered, for the response to be honest about.
        self.last_used = self.lexical.name

    def search(self, query: str, limit: int = 3) -> list[Match]:
        if self.embedding.vectors:
            try:
                found = self.embedding.search(query, limit)
            except Exception:
                # Retrieval is an addition to the page, not the page. A failed
                # lookup should cost the article panel, never the analysis.
                pass
            else:
                self.last_used = self.embedding.name
                return found

        self.last_used = self.lexical.name
        return self.lexical.search(query, limit)


def load_vectors(path: Path = VECTOR_FILE) -> dict[str, list[float]]:
    """The committed vectors, or nothing if they have not been built yet.

    Missing vectors are a normal state, not a fault: the repo clones without
    them until scripts/embed_playbooks.py has run. Returning nothing lets the
    word matcher carry on alone rather than taking the feature down.
    """

    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {k: v for k, v in data.get("vectors", {}).items() if isinstance(v, list)}
