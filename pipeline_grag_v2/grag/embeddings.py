"""Strict BGE-small embedding backend shared by build and runtime."""

from __future__ import annotations

from typing import Iterable

import numpy as np


EMBEDDING_MODEL = "BAAI/bge-small-en-v1.5"
BGE_QUERY_PREFIX = "Represent this sentence for searching relevant passages: "


class BGEEmbedder:
    def __init__(self, model_name: str = EMBEDDING_MODEL, *, allow_download: bool = False) -> None:
        if model_name != EMBEDDING_MODEL:
            raise RuntimeError(
                f"GRAG Fixed requires {EMBEDDING_MODEL!r}; index requested {model_name!r}."
            )
        try:
            from sentence_transformers import SentenceTransformer
            try:
                # Normal runtime path: exact cached model, no network metadata check.
                self.model = SentenceTransformer(model_name, local_files_only=True)
            except Exception:
                if not allow_download:
                    raise
                # The build command may download that same exact model.
                self.model = SentenceTransformer(model_name)
        except Exception as exc:
            raise RuntimeError(
                f"Unable to load required embedding model {model_name!r}; fallback is disabled."
            ) from exc
        self.model_name = model_name
        self.name = f"sentence-transformers:{model_name}"

    def _encode(self, texts: Iterable[str]) -> np.ndarray:
        vectors = self.model.encode(
            list(texts),
            normalize_embeddings=True,
            show_progress_bar=False,
        )
        return np.asarray(vectors, dtype="float32")

    def encode_documents(self, texts: Iterable[str]) -> np.ndarray:
        return self._encode(texts)

    def encode_queries(self, texts: Iterable[str]) -> np.ndarray:
        return self._encode(f"{BGE_QUERY_PREFIX}{text}" for text in texts)


def load_embedder(model_name: str = EMBEDDING_MODEL, *, allow_download: bool = False) -> BGEEmbedder:
    return BGEEmbedder(model_name, allow_download=allow_download)
