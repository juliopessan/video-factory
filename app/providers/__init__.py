from ..config import settings
from .azure_sora import AzureSoraProvider
from .base import (
    DEFAULT_CAPABILITIES,
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
    "AzureSoraProvider",
    "DEFAULT_CAPABILITIES",
    "MediaInput",
    "Mode",
    "ProviderError",
    "VideoProvider",
    "VideoRequest",
    "VideoResult",
    "GeminiOmniProvider",
    "MockProvider",
    "get_provider",
    "capabilities",
    "PROVIDERS",
]

_cache: dict[str, VideoProvider] = {}


PROVIDERS = {
    "gemini": GeminiOmniProvider,   # Gemini Omni 1.1 Flash
    "azure": AzureSoraProvider,     # Sora-2 no Microsoft Foundry
    "mock": MockProvider,           # offline
}


def get_provider(name: str | None = None) -> VideoProvider:
    """Devolve o provider ativo. `auto` escolhe pelo que estiver configurado."""
    name = (name or settings.effective_provider).lower()
    if name not in PROVIDERS:
        raise ProviderError(f"Provider desconhecido: {name}. Use {', '.join(PROVIDERS)}.")
    if name not in _cache:
        _cache[name] = PROVIDERS[name]()
    return _cache[name]


def capabilities(name: str | None = None) -> dict:
    name = (name or settings.effective_provider).lower()
    provider = PROVIDERS.get(name)
    return provider.capabilities() if provider else dict(DEFAULT_CAPABILITIES)
