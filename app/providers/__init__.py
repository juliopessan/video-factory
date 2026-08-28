from ..config import settings
from .base import (
    MediaInput,
    Mode,
    ProviderError,
    VideoProvider,
    VideoRequest,
    VideoResult,
)
from .gemini import GeminiOmniProvider
from .mock import MockProvider

__all__ = [
    "MediaInput",
    "Mode",
    "ProviderError",
    "VideoProvider",
    "VideoRequest",
    "VideoResult",
    "GeminiOmniProvider",
    "MockProvider",
    "get_provider",
]

_cache: dict[str, VideoProvider] = {}


def get_provider(name: str | None = None) -> VideoProvider:
    """Devolve o provider ativo. `auto` cai no mock quando nao ha API key."""
    name = (name or settings.effective_provider).lower()
    if name not in _cache:
        _cache[name] = GeminiOmniProvider() if name == "gemini" else MockProvider()
    return _cache[name]
