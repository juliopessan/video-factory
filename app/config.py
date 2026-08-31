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
    azure_endpoint: str
    azure_api_key: str
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
    def has_azure(self) -> bool:
        return bool(self.azure_endpoint and self.azure_api_key)

    @property
    def effective_provider(self) -> str:
        """`auto`: Gemini se houver chave, senão Azure, senão mock."""
        if self.provider != "auto":
            return self.provider
        if self.api_key:
            return "gemini"
        return "azure" if self.has_azure else "mock"


def get_settings() -> Settings:
    storage = Path(os.environ.get("VF_STORAGE_DIR", "storage"))
    if not storage.is_absolute():
        storage = BASE_DIR / storage
    settings = Settings(
        api_key=os.environ.get("GEMINI_API_KEY", "").strip(),
        azure_endpoint=os.environ.get("AZURE_OPENAI_ENDPOINT", "").strip(),
        azure_api_key=os.environ.get("AZURE_OPENAI_API_KEY", "").strip(),
        provider=os.environ.get("VF_PROVIDER", "auto").strip().lower(),
        model=os.environ.get("VF_MODEL", "gemini-omni-1.1-flash").strip(),
        storage_dir=storage,
        host=os.environ.get("VF_HOST", "127.0.0.1"),
        port=int(os.environ.get("VF_PORT", "8000")),
    )
    settings.media_dir.mkdir(parents=True, exist_ok=True)
    settings.uploads_dir.mkdir(parents=True, exist_ok=True)
    return settings


def update_settings(updates: dict[str, str | None]) -> Settings:
    global settings
    env_file = BASE_DIR / ".env"
    existing_lines: list[str] = []
    if env_file.exists():
        existing_lines = env_file.read_text(encoding="utf-8").splitlines()

    key_map = {
        "gemini_api_key": "GEMINI_API_KEY",
        "provider": "VF_PROVIDER",
        "model": "VF_MODEL",
        "azure_endpoint": "AZURE_OPENAI_ENDPOINT",
        "azure_api_key": "AZURE_OPENAI_API_KEY",
        "azure_deployment": "VF_AZURE_DEPLOYMENT",
        "azure_api_version": "VF_AZURE_API_VERSION",
        "azure_api_style": "VF_AZURE_API_STYLE",
        "ffmpeg": "VF_FFMPEG",
        "ffprobe": "VF_FFPROBE",
    }

    env_updates: dict[str, str] = {}
    for k, v in updates.items():
        if k in key_map and v is not None:
            env_var = key_map[k]
            val = str(v).strip()
            os.environ[env_var] = val
            env_updates[env_var] = val

    new_lines = []
    handled_vars = set()
    for line in existing_lines:
        trimmed = line.strip()
        if trimmed and not trimmed.startswith("#") and "=" in trimmed:
            k, _ = trimmed.split("=", 1)
            var_name = k.strip()
            if var_name in env_updates:
                new_lines.append(f"{var_name}={env_updates[var_name]}")
                handled_vars.add(var_name)
                continue
        new_lines.append(line)

    for var_name, val in env_updates.items():
        if var_name not in handled_vars:
            new_lines.append(f"{var_name}={val}")

    env_file.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
    settings = get_settings()
    return settings


settings = get_settings()
