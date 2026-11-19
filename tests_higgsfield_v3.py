"""Famílias Kling 3.0 / Wan 3.0 no provider Higgsfield (offline).

O caminho do Kling 3.0 vem da documentação pública; o corpo da requisição é
inferido e só o primeiro render real confirma. Este teste fixa o comportamento
escolhido e garante que Wan 2.6 / Hailuo 2.3 seguem estritos como antes.

Roda: `python3 tests_higgsfield_v3.py`.
"""
from __future__ import annotations

import os
import tempfile

os.environ.setdefault("VF_STORAGE_DIR", tempfile.mkdtemp(prefix="vf-hf3-"))

from app.providers.base import MediaInput, ProviderError, VideoRequest  # noqa: E402
from app.providers.higgsfield import HiggsfieldProvider, model_profile  # noqa: E402

failures: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    print(f"{'ok  ' if condition else 'FAIL'} {label}{'' if condition else ' -> ' + detail}")
    if not condition:
        failures.append(label)


def refuses(fn, needle: str) -> bool:
    try:
        fn()
    except ProviderError as exc:
        return needle in str(exc)
    return False


kling = HiggsfieldProvider(key_id="i", key_secret="s", text_model="kling-video/v3.0/std/text-to-video")
url, body = kling.build_request(VideoRequest(prompt=" Um peixe ", mode="text_to_video", duration_seconds=10, aspect_ratio="9:16"))
check("Kling 3.0: caminho do modelo", url == "https://api.higgsfield.ai/kling-video/v3.0/std/text-to-video", url)
check("Kling 3.0: corpo com proporção e sem campos do Wan 2.6",
      body == {"prompt": "Um peixe", "duration": 10, "aspect_ratio": "9:16"}, str(body))
check("Kling 3.0: aceita 3 a 15 s", all(
    kling.build_request(VideoRequest(prompt="x", mode="text_to_video", duration_seconds=n))[1]["duration"] == n
    for n in (3, 8, 15)))
check("Kling 3.0: recusa 2 s e 16 s", all(
    refuses(lambda n=n: kling.build_request(VideoRequest(prompt="x", mode="text_to_video", duration_seconds=n)), "3 a 15")
    for n in (2, 16)))
check("Kling 3.0: recusa proporção fora da lista",
      refuses(lambda: kling.build_request(VideoRequest(prompt="x", mode="text_to_video", aspect_ratio="21:9")), "21:9"))

wan3 = HiggsfieldProvider(key_id="i", key_secret="s", text_model="wan/v3.0/text-to-video")
url, body = wan3.build_request(VideoRequest(prompt="x", mode="text_to_video", duration_seconds=25, resolution="1080p"))
check("Wan 3.0: 25 s em 1080p", body["duration"] == 25 and body["resolution"] == "1080p" and url.endswith("/wan/v3.0/text-to-video"), str(body))
check("Wan 3.0: recusa 360p", refuses(lambda: wan3.build_request(
    VideoRequest(prompt="x", mode="text_to_video", resolution="360p")), "resoluções"))

image = MediaInput("image", b"png", "image/png", "first_frame")
kling_img = HiggsfieldProvider(key_id="i", key_secret="s", image_model="kling-video/v3.0/std/image-to-video")
url, body = kling_img.build_request(
    VideoRequest(prompt="x", mode="image_to_video", duration_seconds=10, media=[image]), image_url="https://cdn.test/f.png")
check("Kling 3.0 image-to-video usa a URL enviada", body["image_url"] == "https://cdn.test/f.png" and url.endswith("/image-to-video"), str(body))
check("image-to-video sem imagem é recusado", refuses(lambda: kling_img.build_request(
    VideoRequest(prompt="x", mode="image_to_video", duration_seconds=10)), "exatamente uma imagem"))

# o contrato estrito do Wan 2.6 / Hailuo 2.3 não mudou
strict = HiggsfieldProvider(key_id="i", key_secret="s")
check("modelos padrão não têm perfil", model_profile(strict.text_model) is None and model_profile(strict.image_model) is None)
check("Wan 2.6 continua só 16:9", refuses(lambda: strict.build_request(
    VideoRequest(prompt="x", mode="text_to_video", aspect_ratio="9:16")), "16:9"))
check("Wan 2.6 continua 5/10/15 s", refuses(lambda: strict.build_request(
    VideoRequest(prompt="x", mode="text_to_video", duration_seconds=8)), "5, 10 ou 15"))

print(f"\n{'All checks passed.' if not failures else str(len(failures)) + ' FAILURE(S)'}")
raise SystemExit(1 if failures else 0)
