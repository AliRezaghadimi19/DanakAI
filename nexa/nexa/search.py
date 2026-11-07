"""In-memory term index over the knowledge base.

Retrieval used to read and tokenize every document on every question. The index maps each term to
the passages that contain it, so a question only touches the passages that share a term with it,
then reads the content of the few winning documents.

Each worker process keeps its own copy. A version stamp in the shared cache tells workers when a
document's text changed; approval and scope are not part of the index and are checked in the
database per question, so publishing a document needs no rebuild.
"""
import threading
import uuid
from collections import defaultdict
from dataclasses import dataclass, field

from django.core.cache import cache
from django.db import transaction
from django.db.models.signals import post_delete, post_save

VERSION_KEY = "nexa:knowledge-index-version"
PASSAGE_STEP = 800
PASSAGE_SIZE = 1000
QUESTION = -1


@dataclass
class Index:
    version: str
    postings: dict = field(default_factory=lambda: defaultdict(list))  # term -> [(pk, passage)]
    titles: dict = field(default_factory=dict)  # pk -> (title, is_file)


_lock = threading.Lock()
_index = None


def current_version():
    version = cache.get(VERSION_KEY)
    if version is None:
        version = uuid.uuid4().hex
        if not cache.add(VERSION_KEY, version, None):
            version = cache.get(VERSION_KEY) or version
    return version


def invalidate():
    """Mark every worker's index stale, now and again once the surrounding transaction commits."""
    cache.set(VERSION_KEY, uuid.uuid4().hex, None)
    transaction.on_commit(lambda: cache.set(VERSION_KEY, uuid.uuid4().hex, None))


def build(version, terms):
    from .models import Knowledge

    index = Index(version)
    rows = Knowledge.objects.filter(enabled=True).order_by("pk").values_list("pk", "title", "content", "source_name")
    for pk, title, content, source_name in rows.iterator():
        index.titles[pk] = (title, bool(source_name))
        if not source_name:
            for term in set(terms(title)):
                index.postings[term].append((pk, QUESTION))
            continue
        for number, start in enumerate(range(0, len(content), PASSAGE_STEP)):
            for term in set(terms(content[start:start + PASSAGE_SIZE])):
                index.postings[term].append((pk, number))
    return index


def get(terms):
    global _index
    version = current_version()
    if _index is not None and _index.version == version:
        return _index
    with _lock:
        if _index is None or _index.version != version:
            _index = build(version, terms)
        return _index


def rank(words, terms):
    """Return [(score, pk, passage)] for documents that match at least half of `words`.

    Matches the earlier full scan: a question entry scores three times its title overlap, a file
    scores its best passage, and the earliest passage wins a tie.
    """
    index = get(terms)
    counts = defaultdict(int)
    for word in words:
        for posting in index.postings.get(word, ()):
            counts[posting] += 1
    best = {}
    for (pk, passage), count in counts.items():
        if count * 2 < len(words):
            continue
        score = count * 3 if passage == QUESTION else count
        current = best.get(pk)
        if current is None or score > current[0] or (score == current[0] and passage < current[1]):
            best[pk] = (score, passage)
    return [(score, pk, passage) for pk, (score, passage) in best.items()]


def _knowledge_changed(**kwargs):
    invalidate()


post_save.connect(_knowledge_changed, sender="nexa.Knowledge", dispatch_uid="nexa-index-save")
post_delete.connect(_knowledge_changed, sender="nexa.Knowledge", dispatch_uid="nexa-index-delete")
