"""Linha de comando: `python -m clipper run|serve|list|export`."""
from __future__ import annotations

import argparse
import sys

from . import MAX_SECONDS, MIN_SECONDS, jobs, pipeline
from .media import ClipperError


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="clipper", description="Cortes verticais de 61–90 s a partir da gravação da live.")
    sub = p.add_subparsers(dest="cmd", required=True)

    run = sub.add_parser("run", help="gera os cortes de uma gravação")
    run.add_argument("video", help="gravação longa da live (mp4, mkv, mov…)")
    run.add_argument("--transcript", help=".srt ou .json já pronto (pula o whisper)")
    run.add_argument("--model", default="small", help="modelo whisper: tiny|base|small|medium|large-v3 (padrão: small)")
    run.add_argument("--language", default="pt", help="idioma da fala (padrão: pt)")
    run.add_argument("--device", default="auto", help="auto|cpu|cuda")
    run.add_argument("-n", "--count", type=int, default=10, help="quantos cortes gerar (padrão: 10)")
    run.add_argument("--min", type=float, default=MIN_SECONDS, dest="min_seconds", help=f"duração mínima (padrão: {MIN_SECONDS:.0f}s)")
    run.add_argument("--max", type=float, default=MAX_SECONDS, dest="max_seconds", help=f"duração máxima (padrão: {MAX_SECONDS:.0f}s)")
    run.add_argument("--facecam", help="x:y:w:h da facecam na gravação, em pixels (ativa o layout empilhado)")
    run.add_argument("--layout", choices=["fill", "blur", "stack"], default="fill", help="fill: tela cheia 9:16 (com --facecam: câmera no topo + gameplay recortado); blur: gameplay inteiro sobre fundo desfocado; stack: gameplay no alto, câmera embaixo")
    run.add_argument("--talk-at", type=float, help="segundo da gravação em que aparece o painel de conversa (chat + câmera grande); ativa a detecção de cena")
    run.add_argument("--font", default="Inter", help="fonte da legenda (padrão: Inter)")
    run.add_argument("--fonts-dir", help="pasta com fontes extras para a legenda")
    run.add_argument("--plan-only", action="store_true", help="só escolhe os trechos, sem renderizar")

    serve = sub.add_parser("serve", help="abre a fila de revisão no navegador")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8100)

    sub.add_parser("list", help="lista os jobs")
    export = sub.add_parser("export", help="copia os cortes aprovados para <job>/export")
    export.add_argument("job_id")
    return p


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        if args.cmd == "run":
            pipeline.run_job(
                args.video, transcript=args.transcript, model=args.model, language=args.language,
                device=args.device, count=args.count, min_seconds=args.min_seconds,
                max_seconds=args.max_seconds, facecam=args.facecam, font=args.font,
                fonts_dir=args.fonts_dir, layout=args.layout, talk_at=args.talk_at, plan_only=args.plan_only,
            )
        elif args.cmd == "serve":
            import uvicorn
            print(f"fila de revisão em http://{args.host}:{args.port}")
            uvicorn.run("clipper.server:app", host=args.host, port=args.port, log_level="warning")
        elif args.cmd == "list":
            for j in jobs.list_jobs():
                print(f"{j['job_id']}  {j['clips']} cortes  "
                      f"({j['approved']} aprovados, {j['pending']} pendentes)  {j['source_name']}")
        elif args.cmd == "export":
            print(jobs.export_approved(args.job_id))
    except ClipperError as exc:
        print(f"erro: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
