"""Configuração em runtime: o que o modal salva precisa chegar em quem gera.

O modal grava a chave no .env e reconstrói `config.settings`. Módulos que
guardassem uma referência ao objeto antigo continuariam com a chave anterior até
o servidor reiniciar — o app dizia "chave configurada" e seguia gerando em mock.

Roda offline: `python3 tests_config.py`.
"""
from __future__ import annotations

import os
import tempfile

os.environ["VF_STORAGE_DIR"] = tempfile.mkdtemp(prefix="vf-config-")
os.environ.pop("GEMINI_API_KEY", None)
os.environ.pop("VF_PROVIDER", None)

from app import config as config_mod  # noqa: E402
from app import providers, textgen  # noqa: E402
from app.config import BASE_DIR  # noqa: E402

falhas: list[str] = []
env_original = (BASE_DIR / ".env").read_text(encoding="utf-8") if (BASE_DIR / ".env").exists() else None


def check(label: str, cond: bool, detalhe: str = "") -> None:
    print(f"{'ok  ' if cond else 'FALHA'} {label}{'' if cond else ' -> ' + str(detalhe)}")
    if not cond:
        falhas.append(label)


try:
    config_mod.update_settings({"gemini_api_key": ""})
    check("sem chave, o provider é mock", config_mod.settings.effective_provider == "mock",
          config_mod.settings.effective_provider)
    check("sem chave, o modelo de texto está indisponível", textgen.available() is False)

    config_mod.update_settings({"gemini_api_key": "chave-de-teste"})
    check("a chave salva chega no config", config_mod.settings.api_key == "chave-de-teste")
    check("a chave salva chega no modelo de texto", textgen.available() is True,
          "textgen segurou uma referência antiga")
    check("a chave salva muda o provider resolvido",
          providers.config.settings.effective_provider == "gemini",
          providers.config.settings.effective_provider)
    check("o provider do Gemini leria a chave nova",
          providers.config.settings.api_key == "chave-de-teste")

    config_mod.update_settings({"provider": "mock"})
    check("provider forçado no modal vale mais que a chave",
          providers.config.settings.effective_provider == "mock",
          providers.config.settings.effective_provider)

    config_mod.update_settings({"ffmpeg": "/usr/bin/ffmpeg", "provider": "auto"})
    check("caminho de ffmpeg salvo vai para o ambiente",
          os.environ.get("VF_FFMPEG") == "/usr/bin/ffmpeg", os.environ.get("VF_FFMPEG"))

    conteudo = (BASE_DIR / ".env").read_text(encoding="utf-8")
    check("o .env não duplica chaves", conteudo.count("GEMINI_API_KEY=") == 1, conteudo)
    check("o .env guarda o que foi salvo", "GEMINI_API_KEY=chave-de-teste" in conteudo)
finally:
    # devolve o .env como estava, para não deixar chave de teste no disco
    env_file = BASE_DIR / ".env"
    if env_original is None:
        env_file.unlink(missing_ok=True)
    else:
        env_file.write_text(env_original, encoding="utf-8")
    os.environ.pop("GEMINI_API_KEY", None)
    os.environ.pop("VF_FFMPEG", None)
    config_mod.settings = config_mod.get_settings()

print("\n" + ("FALHAS: " + ", ".join(falhas) if falhas else "Tudo verde."))
raise SystemExit(1 if falhas else 0)
