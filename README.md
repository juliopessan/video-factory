# Video Factory

Plataforma local de criação de vídeos sobre o **Gemini Omni 1.1 Flash**. Roda inteira na sua
máquina: FastAPI + SQLite + uma interface sem build step. Os vídeos, uploads e o banco ficam em
`./storage`.

O núcleo é um pipeline de quatro passos:

```
1. Contexto  →  2. Storytelling  →  3. Storyboard  →  4. Resultado final
   brief         5 atos (PT-BR)     peças de 10s       cena contínua
                 + direção (EN)     + prompts EN       renderizada
```

## Como rodar

```bash
cd video-factory
cp .env.example .env          # opcional: cole sua GEMINI_API_KEY
./run.sh                      # cria a venv, instala e sobe em http://127.0.0.1:8000
```

Sem `GEMINI_API_KEY` o app sobe no **provider mock**: nada é enviado para a API, e cada peça vira
um clipe sintético (SVG animado com o prompt). Serve para percorrer o fluxo, testar o
encadeamento de cenas e a fila sem gastar cota. Com a chave configurada, o mesmo fluxo chama o
modelo de verdade.

Testes (offline, provider mock):

```bash
python3 tests_smoke.py     # jornada completa: contexto → render → limites de domínio
python3 tests_media.py     # leitura de duração de vídeo e corpo enviado à API por modo
```

## O pipeline

**1. Contexto.** Marca, produto, público, problema, ponto de virada, valor de negócio, CTA,
elenco/figurino, estética base, frame de referência e formato (duração, proporção, resolução).

**2. Storytelling.** Um roteiro em cinco atos — Gancho, Problema Real, Ponto de Virada, Valor de
Negócio e Call to Action — com locução em português e direção de câmera em inglês. Cada ato é
editável antes de seguir.

**3. Storyboard.** Os atos viram peças de 10 segundos. A peça 1 abre a cena; as seguintes
continuam a mesma tomada. Cada peça compila um prompt no template de produção:

```
SCENE CONTEXT · ACTIVE REFERENCE · CHARACTERS · FIRST FRAME · FORMAT MODE
ACTION AND CAMERA SEQUENCE · LIGHTING AND IMAGE QUALITY · PHYSICS · AUDIO
```

Editou o storyboard? "Recompilar prompts" reescreve os prompts a partir do que está na tela.

**4. Resultado final.** A peça 1 é gerada e **cada extensão só entra na fila quando a anterior
termina** — a extensão precisa do `interaction_id` do clipe anterior. O resultado é uma cena
contínua de 10, 20, 30 ou 40 segundos.

## Recursos do Omni 1.1 expostos

| Recurso | Onde | Detalhe |
|---|---|---|
| Extensão de cena | Pipeline e Studio | incrementos de 10s, teto cumulativo de 40s, via `previous_interaction_id` |
| Primeiro + último frame | Studio (`interpolate`) | duas imagens com papéis `first_frame` / `last_frame` |
| Imagem → vídeo | Studio e peça 1 do pipeline | frame de referência do contexto |
| Referências multimodais | Studio (`reference_to_video`) | até 3 referências de vídeo, ≤ 3s cada (duração medida no upload) |
| Rascunho em 360p | Draft room | variações lado a lado, promoção da vencedora para 720p/1080p/4K |
| Upscale | Studio | 1080p e 4K a partir de um clipe pronto |

## Arquitetura

```
app/
  config.py       regras de domínio (10s por extensão, 40s de teto, resoluções) e settings
  db.py           SQLite: projects, generations, assets, pipelines, pipeline_renders
  studio.py       validação, fila (ThreadPoolExecutor) e execução das gerações
  pipeline.py     contexto → storytelling → storyboard → render sequencial
  textgen.py      geração de JSON estruturado para roteiro e storyboard
  mediainfo.py    duração de MP4/MOV/WebM lida do cabeçalho, sem ffmpeg
  providers/
    base.py       contrato VideoRequest / VideoResult
    gemini.py     client.interactions.create + polling até `completed`
    mock.py       clipe sintético para rodar offline
  main.py         API HTTP + entrega da interface
web/              interface (HTML + CSS + JS, sem build)
```

Chamada ao modelo, em resumo:

```python
client.interactions.create(
    model="gemini-omni-1.1-flash",
    previous_interaction_id=parent_interaction_id,   # extensão de cena
    input=[{"type": "image", ...}, {"type": "text", "text": prompt}],
    response_format={"type": "video", "resolution": "720p",
                     "aspect_ratio": "16:9", "duration": "10s", "delivery": "inline"},
    generation_config={"video_config": {"task": "extend"}},
)
```

## Configuração

| Variável | Padrão | Para quê |
|---|---|---|
| `GEMINI_API_KEY` | — | chave da Gemini API; sem ela o app roda em mock |
| `VF_PROVIDER` | `auto` | `auto`, `gemini` ou `mock` |
| `VF_MODEL` | `gemini-omni-1.1-flash` | modelo de vídeo |
| `VF_TEXT_MODEL` | `gemini-flash-latest` | modelo de texto do roteiro/storyboard |
| `VF_STORAGE_DIR` | `storage` | banco, mídias e uploads |
| `VF_HOST` / `VF_PORT` | `127.0.0.1` / `8000` | endereço do servidor |

## Notas

- O custo aparece em **unidades relativas** (360p = 1/3 de 720p), não em dólares: serve para
  comparar rascunho e render final, não para faturamento.
- O mock grava SVG animado, não MP4 — a interface detecta o mime type e exibe como imagem.
- **Duração de referência.** Todo vídeo enviado tem a duração lida do cabeçalho do arquivo
  (`app/mediainfo.py`: átomo `mvhd` do MP4/MOV, elemento `Duration` do WebM) e gravada no banco.
  Referência acima de 3s é recusada com o nome do arquivo e a duração medida. Contêiner que não
  sabemos ler devolve duração desconhecida e passa sem o limite — melhor do que recusar um
  arquivo válido por não conseguir medi-lo.
- **Upscale.** A documentação anuncia 1080p e 4K, mas não nomeia uma task própria para isso.
  Aqui o upscale é um novo render da mesma interação (`previous_interaction_id`) pedindo a
  resolução maior, **sem** `video_config.task` — o modelo determina o modo pelo texto e pela
  mídia de entrada, como o SDK documenta. Se a API passar a expor uma task dedicada, o ajuste
  é uma linha em `providers/base.py` (`TASK_BY_MODE["upscale"]`).
