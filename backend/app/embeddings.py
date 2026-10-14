"""Local embeddings with fastembed (ONNX on CPU).

Why local: zero per query cost and no third party API in the request path.
Why bge small: strong retrieval quality for its size, 384 dimensions keep the index small,
and it is small enough to bake into the Docker image so cold starts do not download it.
"""
from functools import lru_cache

import numpy as np
from fastembed import TextEmbedding

from app.config import get_settings


@lru_cache
def _model() -> TextEmbedding:
    s = get_settings()
    return TextEmbedding(model_name=s.embedding_model, cache_dir=s.model_cache_dir)


def embed_query(text: str) -> np.ndarray:
    # query_embed applies the query side formatting the model was trained with.
    return next(iter(_model().query_embed(text)))


def embed_passages(texts: list[str], batch_size: int = 64) -> list[np.ndarray]:
    return list(_model().passage_embed(texts, batch_size=batch_size))


def warm_up() -> None:
    """Load the model at startup so the first user request is not slower."""
    embed_query("warm up")
