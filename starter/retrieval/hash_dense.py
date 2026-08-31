"""Offline hashed character n-gram dense lane inspired by ShopPilot.

This is intentionally experimental and never enabled unless explicitly
requested. It provides paraphrase recall without model downloads.
"""
from __future__ import annotations

import hashlib
import math
import re
import struct

import numpy as np

TOKEN_RE = re.compile(r"[a-z0-9]+", re.I)
DIM = 512


def _grams(text: str) -> list[str]:
    words = TOKEN_RE.findall((text or "").lower())
    result: list[str] = []
    for word in words:
        if len(word) < 3:
            result.append("w:" + word)
            continue
        padded = "#" + word + "#"
        result.extend(padded[i:i + 3] for i in range(len(padded) - 2))
    return result


def encode(text: str) -> np.ndarray:
    vector = np.zeros(DIM, dtype=np.float32)
    for gram in _grams(text):
        digest = hashlib.blake2b(gram.encode(), digest_size=8).digest()
        value = struct.unpack(">Q", digest)[0]
        vector[value % DIM] += 1.0 if value >> 63 == 0 else -1.0
    norm = float(np.linalg.norm(vector))
    return vector / norm if norm else vector


class HashDenseIndex:
    def __init__(self, rows: list[tuple[str, str]]) -> None:
        self.ids = [row[0] for row in rows]
        self.matrix = np.vstack([encode(row[1]) for row in rows]).astype(np.float32)

    def search(self, query: str, top_n: int) -> list[str]:
        scores = self.matrix @ encode(query)
        top_n = min(max(int(top_n), 1), len(self.ids))
        indices = np.argpartition(-scores, top_n - 1)[:top_n]
        indices = indices[np.argsort(-scores[indices], kind="stable")]
        return [self.ids[int(index)] for index in indices]
