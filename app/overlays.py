"""Camadas de marca renderizadas com Remotion.

O modelo generativo erra logo e tipografia; essa parte é desenhada em React e
composta por cima do filme no passo 5. As composições ficam em `brand-overlays/`
(LowerThird e Packshot), com fundo transparente.

Requer Node e `npm install` dentro de `brand-overlays/`. Sem isso, a interface
mostra a opção desabilitada — nada quebra.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path

from . import config
from .config import BASE_DIR

PROJECT_DIR = Path(os.environ.get("VF_REMOTION_PROJECT") or BASE_DIR / "brand-overlays")
ENTRY = "src/index.ts"
COMPOSITIONS = ("LowerThird", "Packshot")
RENDER_TIMEOUT_SECONDS = 1800


def browser_executable() -> str | None:
    """Remotion precisa do chrome-headless-shell (o headless antigo)."""
    return os.environ.get("VF_REMOTION_BROWSER") or None


def available() -> bool:
    return bool(shutil.which("npx")) and (PROJECT_DIR / "node_modules").is_dir()


def _cache_dir() -> Path:
    path = config.settings.storage_dir / "overlays"
    path.mkdir(parents=True, exist_ok=True)
    return path


def render(composition: str, props: dict | None = None) -> Path:
    """Renderiza a composição em WebM com alpha. O resultado é cacheado por props."""
    if composition not in COMPOSITIONS:
        raise RuntimeError(f"Composição desconhecida: {composition}. Use {', '.join(COMPOSITIONS)}.")
    if not available():
        raise RuntimeError(
            f"Remotion não está pronto. Rode `npm install` em {PROJECT_DIR} (precisa de Node 18+)."
        )
    props = props or {}
    key = hashlib.sha256(
        f"{composition}:{json.dumps(props, sort_keys=True, ensure_ascii=False)}".encode()
    ).hexdigest()[:16]
    destination = _cache_dir() / f"{composition}_{key}.webm"
    if destination.exists():
        return destination

    command = [
        "npx", "remotion", "render", ENTRY, composition, str(destination),
        "--codec=vp8", "--pixel-format=yuva420p", "--log=error",
    ]
    if props:
        command.append(f"--props={json.dumps(props, ensure_ascii=False)}")
    browser = browser_executable()
    if browser:
        command.append(f"--browser-executable={browser}")

    result = subprocess.run(
        command, cwd=PROJECT_DIR, capture_output=True, text=True, timeout=RENDER_TIMEOUT_SECONDS
    )
    if result.returncode != 0 or not destination.exists():
        raise RuntimeError(f"Remotion falhou: {(result.stderr or result.stdout)[-400:]}")
    return destination
