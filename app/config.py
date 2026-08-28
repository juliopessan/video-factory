"""Configuracao carregada de variaveis de ambiente (e do .env, se existir)."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def _load_dotenv() -> None:
    env_file = BASE_DIR / ".env"
    if not env_file.exists():
        return
    for raw in env_file.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_dotenv()

# Regras de dominio do Omni 1.1 Flash (conforme documentacao do modelo).
CLIP_SECONDS = 8               # duracao base de um clipe gerado
EXTENSION_SECONDS = 10         # extensoes acontecem em incrementos de 10s
MAX_CUMULATIVE_SECONDS = 40    # limite cumulativo de uma cena estendida
MAX_REFERENCE_VIDEOS = 3       # ate 3 referencias de video
MAX_REFERENCE_VIDEO_SECONDS = 3
RESOLUTIONS = ("360p", "720p", "1080p", "4k")
ASPECT_RATIOS = ("16:9", "9:16")

# Custo relativo por segundo, em "unidades" (360p = 1/3 do 720p, conforme o anuncio).
# Nao sao precos em dolar: sirvam apenas para comparar rascunho vs. render final.
COST_UNITS_PER_SECOND = {"360p": 1.0, "720p": 3.0, "1080p": 4.5, "4k": 6.0}


@dataclass(frozen=True)
class Settings:
    api_key: str
    provider: str
    model: str
    storage_dir: Path
    host: str
    port: int

    @property
    def media_dir(self) -> Path:
        return self.storage_dir / "media"

    @property
    def uploads_dir(self) -> Path:
        return self.storage_dir / "uploads"

    @property
    def db_path(self) -> Path:
        return self.storage_dir / "factory.db"

    @property
    def effective_provider(self) -> str:
        if self.provider == "auto":
            return "gemini" if self.api_key else "mock"
        return self.provider


def get_settings() -> Settings:
    storage = Path(os.environ.get("VF_STORAGE_DIR", "storage"))
    if not storage.is_absolute():
        storage = BASE_DIR / storage
    settings = Settings(
        api_key=os.environ.get("GEMINI_API_KEY", "").strip(),
        provider=os.environ.get("VF_PROVIDER", "auto").strip().lower(),
        model=os.environ.get("VF_MODEL", "gemini-omni-1.1-flash").strip(),
        storage_dir=storage,
        host=os.environ.get("VF_HOST", "127.0.0.1"),
        port=int(os.environ.get("VF_PORT", "8000")),
    )
    settings.media_dir.mkdir(parents=True, exist_ok=True)
    settings.uploads_dir.mkdir(parents=True, exist_ok=True)
    return settings


settings = get_settings()
