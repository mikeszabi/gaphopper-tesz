from types import SimpleNamespace

from gaphopper.merge import Resolver


def test_search_and_write_reuse_one_embedding_per_text():
    calls = []

    def encode(text):
        calls.append(text)
        return [1.0, 0.0]

    ctx = SimpleNamespace(embedder=SimpleNamespace(enabled=True, encode=encode),
                          graph=SimpleNamespace(vector_candidates=lambda *args: []))
    resolver = Resolver(ctx)
    assert resolver._candidates("TESZ", "azonos szöveg") == ([], True)
    assert resolver.encode("azonos szöveg") == [1.0, 0.0]
    assert calls == ["azonos szöveg"]
    # A different job gets a fresh cache.
    Resolver(ctx).encode("azonos szöveg")
    assert len(calls) == 2


def test_repeated_candidates_reuse_context():
    calls = []

    def context(entity_id):
        calls.append(entity_id)
        return {"tesz_type": "VERIFY"}

    resolver = Resolver(SimpleNamespace(graph=SimpleNamespace(mag_context=context)))
    assert resolver._context("existing") == resolver._context("existing")
    resolver._context("another")
    assert calls == ["existing", "another"]
