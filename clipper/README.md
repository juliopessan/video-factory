# Clipper

Pega a gravação longa de uma live e devolve cortes verticais de 61–90 s, com legenda queimada, numa fila de aprovação local. Design e decisões em [`docs/clipper-design.md`](../docs/clipper-design.md).

## Uso

```bash
./run_clipper.sh run minha-live.mp4          # gera os cortes (a 1ª vez instala as dependências)
./run_clipper.sh serve                       # fila de revisão em http://127.0.0.1:8100
./run_clipper.sh list                        # jobs e quantos cortes foram aprovados
./run_clipper.sh export <job-id>             # copia os aprovados + legendas.txt
```

Na fila: assistir, aprovar ou rejeitar, editar título e hashtags, ajustar início/fim e refazer o corte. O selo amarelo avisa quando um corte ficou abaixo de 1 minuto (não entra no Creator Rewards).

## Opções úteis

| Opção | Para quê |
|---|---|
| `-n 15` | quantos cortes gerar (padrão 10) |
| `--facecam 1500:800:400:280` | x:y:w:h da facecam na gravação: gameplay no alto, facecam embaixo |
| `--model base` | whisper mais leve para CPU (`small` é o padrão; `large-v3` só com GPU) |
| `--device cuda` | usa a GPU, se houver |
| `--transcript live.srt` | usa uma legenda pronta e pula o whisper |
| `--plan-only` | só escolhe os trechos, sem renderizar (rápido, bom para testar) |

## Como escolhe os trechos

Por segundo: energia do áudio (relativa à própria live), palavras de reação e ação em PT-BR, exclamações e densidade de fala. A janela vale 60% da média e 40% do pico, com penalidade para silêncio. Começa e termina em limite de frase, nunca sai com menos de 61 s. O léxico está em `clipper/scoring.py` (`KEYWORDS`) e é para ser ajustado ao jeito do Arthur falar.

## Onde ficam os arquivos

`storage/clipper/<job-id>/` — `manifest.json`, `transcript.json` (cache: rodar de novo no mesmo arquivo não retranscreve), `clips/` e `export/`.

## Limites conhecidos

- A transcrição é a etapa lenta: em CPU, `small` leva de uma a algumas horas para uma live de 2 h. Com GPU, minutos.
- O corte é fiel à live. Zoom, música e memes ficam de fora.
- Postagem é manual. A API do TikTok exige auditoria do app antes de publicar de forma pública.
