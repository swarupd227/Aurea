"""Local embeddings for firm-research RAG. Self-contained (no external API).

Uses fastembed (BAAI/bge-small-en-v1.5, 384-dim, CPU). If the model can't be loaded the
service degrades gracefully: embeddings become None and retrieval falls back to lexical
search over the chunk text."""
from __future__ import annotations

from app.core.logging import get_logger
from app.models.knowledge import EMBED_DIM

log = get_logger("aurea.embed")


class EmbeddingService:
    def __init__(self) -> None:
        self._model = None
        self._tried = False

    def _ensure(self):
        if self._model is None and not self._tried:
            self._tried = True
            try:
                from fastembed import TextEmbedding

                self._model = TextEmbedding(model_name="BAAI/bge-small-en-v1.5")
                log.info("embedding_model_loaded")
            except Exception as exc:  # pragma: no cover - environment dependent
                log.warning("embedding_model_unavailable", error=str(exc))
                self._model = None
        return self._model

    @property
    def available(self) -> bool:
        return self._ensure() is not None

    def embed(self, texts: list[str]) -> list[list[float] | None]:
        model = self._ensure()
        if model is None:
            return [None for _ in texts]
        return [list(map(float, vec)) for vec in model.embed(texts)]

    def embed_one(self, text: str) -> list[float] | None:
        return self.embed([text])[0]


embedding_service = EmbeddingService()
__all__ = ["embedding_service", "EMBED_DIM"]
