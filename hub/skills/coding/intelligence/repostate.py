"""Warm per-repository state held by the long-lived service: the CodeIndex of each recently used repository, refreshed incrementally.

A refresh stats every file in the scoped inventory and re-parses only those whose size or modification time changed (unchanged entries come straight from memory,
without re-reading the on-disk cache). Nothing else is remembered about a repository: no model-written notes, only what the host derives from the files. The cache is
bounded (a few repositories, least recently used evicted) and safe to drop at any time."""
import threading
import time
from collections import OrderedDict
from .codeindex import CodeIndex

MAX_REPOS = 3

class RepoStates:
    def __init__(self, limit=MAX_REPOS):
        self.limit, self.lock, self.states = limit, threading.Lock(), OrderedDict()
        self.stats = {'hits': 0, 'misses': 0}

    def index(self, files):
        """The index for the repository behind `files`, refreshed against the file system."""
        key = str(files.root)
        with self.lock:
            previous = self.states.pop(key, None)
            self.stats['hits' if previous is not None else 'misses'] += 1
            fresh = CodeIndex.load(files, previous)
            fresh.refreshed = time.time()
            self.states[key] = fresh
            while len(self.states) > self.limit:
                self.states.popitem(last=False)
            return fresh

    def forget(self, root=None):
        with self.lock:
            if root is None:
                self.states.clear()
            else:
                self.states.pop(str(root), None)

STATES = RepoStates()
