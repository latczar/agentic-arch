"""The corpus, the parsing, and the retrievers that search it.

None of these call an API. The embedding retriever is handed a function that
returns vectors from a dict, which is the whole reason it takes one.

What is deliberately NOT here is whether retrieval finds the right article. That
is a question about quality rather than correctness, it is answered by a scored
run over a labelled set, and asserting it in a unit test would turn every honest
improvement in a retriever into a failing build.
"""

from __future__ import annotations

import math

import pytest

from app.playbooks import (
    EmbeddingRetriever,
    LexicalRetriever,
    Playbook,
    _parse,
    _tokenise,
    cosine,
    load_playbooks,
    load_vectors,
)

CORPUS = load_playbooks()


# --- The articles themselves -------------------------------------------------


def test_the_corpus_is_actually_there():
    assert len(CORPUS) >= 10


def test_every_article_carries_what_the_retrievers_need():
    for playbook in CORPUS:
        assert playbook.id and " " not in playbook.id
        assert playbook.title
        assert playbook.also_called, f"{playbook.id} has no aliases to match on"
        assert len(playbook.body) > 400, f"{playbook.id} is too thin to be useful"


def test_ids_are_unique():
    """Two articles sharing an id means one of them can never be retrieved."""

    ids = [p.id for p in CORPUS]
    assert len(ids) == len(set(ids))


def test_every_article_says_where_a_person_is_needed():
    """The corpus exists to surface the step somebody forgot to mention."""

    for playbook in CORPUS:
        assert "person" in playbook.body.lower() or "people" in playbook.body.lower()


def test_frontmatter_is_read_and_stripped_from_the_body():
    parsed = _parse(
        "---\nid: a-thing\ntitle: A Thing\nalso_called: one, two\n---\n\n# A Thing\n\nBody.",
        "fallback",
    )

    assert parsed.id == "a-thing"
    assert parsed.title == "A Thing"
    assert parsed.also_called == ("one", "two")
    assert "also_called" not in parsed.body


def test_a_file_without_frontmatter_still_loads():
    """A dropped article should degrade, not take the corpus down."""

    parsed = _parse("# Just a heading\n\nAnd some words.", "rent-collection")

    assert parsed.id == "rent-collection"
    assert parsed.also_called == ()


# --- Tokenising --------------------------------------------------------------


def test_plurals_fold_onto_the_singular():
    """Otherwise half the corpus is unreachable by the words people type."""

    assert _tokenise("invoices") == _tokenise("invoice")
    assert _tokenise("certificates") == _tokenise("certificate")
    assert _tokenise("tenancies") == _tokenise("tenancy")


def test_a_double_s_is_not_mistaken_for_a_plural():
    assert _tokenise("address") == ["address"]


def test_stopwords_go_and_the_words_that_matter_stay():
    words = _tokenise("I have to go and check the rent and then pay it")

    assert "rent" in words and "pay" in words and "check" in words
    assert "the" not in words and "and" not in words


# --- Word matching -----------------------------------------------------------


def test_it_finds_an_obvious_article():
    found = LexicalRetriever(CORPUS).search("supplier invoices arriving by email")

    assert found
    assert found[0].playbook.id == "supplier-invoices"


def test_results_come_back_strongest_first():
    found = LexicalRetriever(CORPUS, threshold=0.0).search("rent arrears chase", limit=5)

    assert [m.score for m in found] == sorted((m.score for m in found), reverse=True)


def test_nothing_comes_back_from_below_the_threshold():
    """The half of the job a retriever that always answers gets wrong."""

    picky = LexicalRetriever(CORPUS, threshold=1000.0)
    assert picky.search("supplier invoices arriving by email") == []


def test_a_query_sharing_no_words_with_anything_returns_nothing():
    found = LexicalRetriever(CORPUS).search("xylophone quartet rehearsal schedule")
    assert found == []


def test_the_limit_is_respected():
    found = LexicalRetriever(CORPUS, threshold=0.0).search("rent payment tenant", limit=2)
    assert len(found) == 2


def test_ties_break_the_same_way_every_time():
    """A retriever whose output order wobbles cannot be scored against a baseline."""

    retriever = LexicalRetriever(CORPUS, threshold=0.0)
    runs = [[m.playbook.id for m in retriever.search("property", limit=5)] for _ in range(3)]

    assert runs[0] == runs[1] == runs[2]


def test_an_empty_corpus_does_not_explode():
    """It is the state of a fresh clone before anybody writes an article."""

    assert LexicalRetriever(()).search("anything") == []


# --- Embeddings --------------------------------------------------------------


def test_cosine_of_a_vector_with_itself_is_one():
    assert cosine([1.0, 2.0, 3.0], [1.0, 2.0, 3.0]) == pytest.approx(1.0)


def test_cosine_of_perpendicular_vectors_is_zero():
    assert cosine([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)


def test_cosine_ignores_length():
    """Truncated vectors need renormalising, so this must not depend on size."""

    assert cosine([3.0, 0.0], [0.5, 0.0]) == pytest.approx(1.0)


def test_cosine_of_a_zero_vector_is_zero_rather_than_an_error():
    assert cosine([0.0, 0.0], [1.0, 1.0]) == 0.0


def fake_corpus() -> tuple[Playbook, ...]:
    return (
        Playbook("near", "Near", ("a",), "body " * 100),
        Playbook("far", "Far", ("b",), "body " * 100),
    )


def test_the_embedding_retriever_ranks_by_similarity():
    books = fake_corpus()
    vectors = {"near": [1.0, 0.0], "far": [0.0, 1.0]}

    retriever = EmbeddingRetriever(
        embed_query=lambda _: [0.96, 0.28],
        playbooks=books,
        vectors=vectors,
        threshold=0.5,
    )
    found = retriever.search("whatever", limit=2)

    assert found[0].playbook.id == "near"
    assert len(found) == 1, "the far one is below threshold and should be dropped"


def test_missing_vectors_leave_the_feature_off_rather_than_broken():
    """A fresh clone has no vectors.json until the build script has run."""

    retriever = EmbeddingRetriever(
        embed_query=lambda _: [1.0, 0.0],
        playbooks=fake_corpus(),
        vectors={},
    )

    assert retriever.search("anything") == []


def test_an_article_with_no_vector_is_skipped_not_guessed_at():
    """Somebody adds an article and forgets to rerun the build script."""

    retriever = EmbeddingRetriever(
        embed_query=lambda _: [1.0, 0.0],
        playbooks=fake_corpus(),
        vectors={"near": [1.0, 0.0]},
        threshold=0.5,
    )
    found = retriever.search("anything", limit=5)

    assert [m.playbook.id for m in found] == ["near"]


def test_load_vectors_survives_a_missing_file(tmp_path):
    assert load_vectors(tmp_path / "nope.json") == {}


def test_load_vectors_survives_a_corrupt_file(tmp_path):
    broken = tmp_path / "vectors.json"
    broken.write_text("{ not json at all", encoding="utf-8")

    assert load_vectors(broken) == {}


# --- The two texts are not the same ------------------------------------------


def test_the_word_matcher_gets_the_aliases_twice_and_the_encoder_once():
    """Deliberate, and the comparison between them is void if it ever changes.

    Repeating aliases lifts a short, distinctive phrase in a bag-of-words score
    and does nothing for a sentence encoder. Applying it to both would mean the
    measured difference included a trick rather than the method.
    """

    book = Playbook("x", "Title", ("alias one",), "Body text.")

    assert book.searchable.count("alias one") == 2
    assert book.document.count("alias one") == 1


def test_the_vector_file_matches_the_articles_on_disk():
    """A stale vector is a wrong answer that looks exactly like a right one.

    Edit an article without rerunning scripts/embed_playbooks.py and retrieval
    keeps working, keeps returning confident scores, and is quietly searching
    text that no longer exists. Nothing else would notice.
    """

    import hashlib
    import json

    from app.playbooks import VECTOR_FILE

    if not VECTOR_FILE.is_file():
        pytest.skip("vectors have not been built in this checkout")

    stored = json.loads(VECTOR_FILE.read_text(encoding="utf-8"))
    fingerprints = stored.get("fingerprints", {})

    for playbook in CORPUS:
        current = hashlib.sha256(playbook.document.encode("utf-8")).hexdigest()[:16]
        assert fingerprints.get(playbook.id) == current, (
            f"{playbook.id} has changed since it was embedded. "
            "Rerun scripts/embed_playbooks.py."
        )


def test_every_article_has_a_vector():
    import json

    from app.playbooks import VECTOR_FILE

    if not VECTOR_FILE.is_file():
        pytest.skip("vectors have not been built in this checkout")

    stored = json.loads(VECTOR_FILE.read_text(encoding="utf-8"))
    vectors = stored.get("vectors", {})

    missing = [p.id for p in CORPUS if p.id not in vectors]
    assert not missing, f"no vector for: {missing}"


def test_the_vectors_are_the_width_they_claim():
    import json

    from app.playbooks import VECTOR_FILE

    if not VECTOR_FILE.is_file():
        pytest.skip("vectors have not been built in this checkout")

    stored = json.loads(VECTOR_FILE.read_text(encoding="utf-8"))
    width = stored["dimensions"]

    for name, vector in stored["vectors"].items():
        assert len(vector) == width, f"{name} is {len(vector)}, not {width}"
        assert math.isfinite(sum(vector))


# --- Choosing between them ---------------------------------------------------


def test_the_fallback_prefers_embeddings_when_it_has_vectors():
    from app.playbooks import FallbackRetriever

    retriever = FallbackRetriever(embed_query=lambda _: [1.0, 0.0], playbooks=fake_corpus())
    retriever.embedding.vectors = {"near": [1.0, 0.0], "far": [0.0, 1.0]}
    retriever.embedding.threshold = 0.5

    found = retriever.search("anything")

    assert retriever.last_used == "embeddings"
    assert [m.playbook.id for m in found] == ["near"]


def test_it_drops_to_word_matching_when_there_are_no_vectors():
    """The state of a fresh clone, and of anyone without an API key."""

    from app.playbooks import FallbackRetriever

    retriever = FallbackRetriever(embed_query=lambda _: [1.0, 0.0], playbooks=CORPUS)
    retriever.embedding.vectors = {}

    found = retriever.search("supplier invoices arriving by email")

    assert retriever.last_used == "bm25"
    assert found and found[0].playbook.id == "supplier-invoices"


def test_a_failing_embedding_call_costs_the_panel_and_not_the_page():
    """The endpoint being unwell must not take retrieval down with it."""

    from app.llm.base import LLMError
    from app.playbooks import FallbackRetriever

    def refuses(_):
        raise LLMError("the endpoint is having a moment")

    retriever = FallbackRetriever(embed_query=refuses, playbooks=CORPUS)
    retriever.embedding.vectors = {p.id: [1.0] for p in CORPUS}

    found = retriever.search("supplier invoices arriving by email")

    assert retriever.last_used == "bm25"
    assert found and found[0].playbook.id == "supplier-invoices"
