# Video Factory

Plataforma local de criação de vídeos. Dois provedores de vídeo — **Gemini Omni 1.1 Flash** e
**Sora-2 no Microsoft Foundry** — e todo o resto roda na sua máquina: FastAPI + SQLite + uma
interface sem build step. Vídeos, uploads e banco ficam em `./storage`.

O núcleo é um pipeline de quatro passos:

```
1. Contexto → 2. Storytelling → 3. Storyboard → 4. Resultado final → 5. Pós-produção
   brief       5 atos (PT-BR)    peças de 10s     cena contínua        legendas, áudio,
               + direção (EN)    + prompts EN     renderizada          16:9 / 9:16 / 1:1
```

Requisito opcional: **FFmpeg** no PATH (`apt install ffmpeg` / `brew install ffmpeg`) para o
passo 5. Sem ele, os passos 1–4 funcionam e a pós-produção aparece desabilitada com o aviso.

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
python3 tests_post.py      # legendas, argv do FFmpeg e um export real de ponta a ponta
python3 tests_azure.py     # provider Sora-2 com transporte HTTP falso
python3 tests_keyframe.py  # jornada com provider que não estende cena (precisa de FFmpeg)
python3 tests_overlays.py  # camada Remotion e composição sobre o filme
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
termina** — a extensão precisa do `interaction_id` do clipe anterior. Cada extensão devolve o
**filme acumulado** (10s → 20s → 30s), não apenas o trecho novo: a última peça pronta já é o
filme inteiro, sem concatenação. Verificado contra a API: 30s em 3 peças, ~174s de render em 360p.

**5. Pós-produção.** Acabamento determinístico com FFmpeg local — nada de modelo aqui:

- **Legendas** tiradas da própria locução do storyboard (sem transcrição: as marcas de tempo
  saem do plano). Locução longa vira várias legendas dentro da janela da peça, nenhuma abaixo
  de 1,2s, quebra de linha por formato (42 colunas no 16:9, 26 no 9:16).
- **Áudio** normalizado a −14 LUFS (EBU R128), com fade in/out opcional.
- **Formatos** 16:9, 9:16 e 1:1, em `crop` (preenche a tela) ou `pad` (preserva o quadro).
  Packshot com logo pede `pad`: o recorte central de um 16:9 come as pontas da marca.
- Saída H.264/AAC com `+faststart`, pronta para publicar, baixável na interface.

## Provedores de vídeo

| | Gemini Omni 1.1 Flash | Sora-2 (Microsoft Foundry) |
|---|---|---|
| `VF_PROVIDER` | `gemini` | `azure` |
| Credencial | `GEMINI_API_KEY` | `AZURE_OPENAI_ENDPOINT` + `AZURE_OPENAI_API_KEY` |
| Extensão de cena | sim, via `previous_interaction_id` (cada peça devolve o filme acumulado) | não — o pipeline encadeia por **keyframe** |
| Resoluções | 360p → 4K | 1280x720 / 720x1280 |
| Durações | livres | 4, 8 ou 12s (arredonda para a mais próxima) |
| Referência de vídeo | até 3, ≤ 3s | imagem (`input_reference`) |
| Edição generativa | tasks `edit` / `extend` | *remix* de um vídeo gerado |

O pipeline lê as capacidades do provider e muda o plano de render sozinho: quem estende cena
produz uma tomada contínua; quem não estende recebe, a cada peça, o **último frame da peça
anterior** como primeiro frame, e o passo 5 emenda tudo num filme só. Trocar de provider não
muda nada nos passos 1–3.

**Sobre o Sora-2:** o acesso é gated (formulário para clientes MCA-E/EA) e a API está em preview,
com duas formas em circulação — `/openai/v1/videos` e `/openai/v1/video/generations`. As duas
estão implementadas e `VF_AZURE_API_STYLE` escolhe. O provider foi testado com transporte HTTP
falso (`tests_azure.py`), **não contra um deployment real**: quando você tiver acesso liberado,
o primeiro render dirá se o caminho e o corpo estão certos.

## Recursos do Omni 1.1 expostos

| Recurso | Onde | Detalhe |
|---|---|---|
| Extensão de cena | Pipeline e Studio | incrementos de 10s, teto cumulativo de 40s, via `previous_interaction_id` |
| Primeiro + último frame | Studio (`interpolate`) | duas imagens com papéis `first_frame` / `last_frame` |
| Imagem → vídeo | Studio e peça 1 do pipeline | frame de referência do contexto |
| Referências multimodais | Studio (`reference_to_video`) | até 3 referências de vídeo, ≤ 3s cada (duração medida no upload) |
| Rascunho em 360p | Draft room | variações lado a lado, promoção da vencedora para 720p/1080p/4K |
| Upscale | Studio | 1080p e 4K a partir de um clipe pronto |

## Edição automática: Kinocut + Remotion

Três edições diferentes, três caminhos:

- **Generativa** (mudar o que acontece no plano) — é o próprio modelo: tasks `edit` e `extend`
  do Omni, *remix* no Sora-2.
- **Mecânica** (cortar, juntar, mixar, legendar, versionar) — é o passo 5, FFmpeg direto em
  `app/postproduction.py`: determinístico, testável, sem protocolo no meio.
- **Exploratória** (dirigida por conversa: *"corta os 4s mortos da peça 2 e põe legenda"*) — é
  onde MCP entra. O [`.mcp.json`](.mcp.json) da raiz já vem com os dois servidores configurados.

### Kinocut — edição por conversa

[Kinocut](https://kinocut.dev) é um servidor MCP local com FFmpeg tipado e guardrails: 196 tools
(trim, merge, subtitles, overlay, quality-check, repurpose para Shorts/Reels). Nada sai da máquina.

```bash
pip install kinocut     # precisa de FFmpeg no PATH
kino doctor             # diz exatamente o que falta
```

Cliente MCP: `kino --mcp` (é o que está no `.mcp.json`).

### Remotion — a camada de marca

O modelo generativo erra logo e tipografia. Essa parte é desenhada em React, com fundo
transparente, e composta sobre o filme pelo passo 5:

```bash
cd brand-overlays && npm install
```

Duas composições prontas em `brand-overlays/src`: **LowerThird** (marca + produto, entra
animado no rodapé) e **Packshot** (marca + claim, para o fecho). As props saem do contexto do
filme; a cor de destaque vem de `VF_BRAND_ACCENT`. Na pós-produção, escolha a camada em
"Camada de marca" — a legenda sobe sozinha para não colidir com o lower third.

O render do Remotion precisa do `chrome-headless-shell` (o headless antigo). Se o Chrome do
sistema não servir, aponte `VF_REMOTION_BROWSER` para um binário `headless_shell`.

Dois detalhes de composição que custaram caro e estão resolvidos em `postproduction.composite`:
sem `-vcodec libvpx` na entrada e `format=yuva420p` no filtro, o alfa do VP8 se perde e a camada
vira um retângulo opaco; e sem `scale2ref`, uma camada 1080p sobre um filme 360p aparece só pelo
canto — ou seja, não aparece.

Cloudinary (no diretório de conectores do Claude) resolve entrega/CDN e variantes por URL — vale
quando entrar distribuição. Descript e Riverside são fortes em edição por transcrição, mais úteis
para conteúdo falado longo que para um filme de 30s.

Kinocut e Remotion são software de terceiros: aqui foram instalados e exercitados de ponta a
ponta, mas não auditados linha a linha.

## Arquitetura

```
app/
  config.py       regras de domínio (10s por extensão, 40s de teto, resoluções) e settings
  db.py           SQLite: projects, generations, assets, pipelines, pipeline_renders
  studio.py       validação, fila (ThreadPoolExecutor) e execução das gerações
  pipeline.py     contexto → storytelling → storyboard → render sequencial
  textgen.py      geração de JSON estruturado para roteiro e storyboard
  mediainfo.py    duração de MP4/MOV/WebM lida do cabeçalho, sem ffmpeg
  postproduction.py  passo 5: legendas, áudio, formatos, montagem e composição
  overlays.py     camadas de marca renderizadas com Remotion
  providers/
    base.py       contrato VideoRequest / VideoResult
    gemini.py     client.interactions.create + polling até `completed`
    azure_sora.py Sora-2 no Foundry: criar job, consultar, baixar
    mock.py       clipe sintético para rodar offline
  main.py         API HTTP + entrega da interface
web/              interface (HTML + CSS + JS, sem build)
brand-overlays/   projeto Remotion: LowerThird e Packshot (fundo transparente)
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
| `VF_STORAGE_DIR` | `storage` | banco, mídias, uploads e exports |
| `VF_FFMPEG` / `VF_FFPROBE` | do `PATH` | binários usados no passo 5 |
| `AZURE_OPENAI_ENDPOINT` / `AZURE_OPENAI_API_KEY` | — | recurso do Foundry com o sora-2 |
| `VF_AZURE_DEPLOYMENT` | `sora-2` | nome do deployment no Foundry |
| `VF_AZURE_API_STYLE` | `videos` | `videos` ou `generations` |
| `VF_REMOTION_BROWSER` | do sistema | caminho de um `chrome-headless-shell` |
| `VF_BRAND_ACCENT` | `#0f6cbd` | cor de destaque das camadas de marca |
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
- **Continuações não levam task.** A API recusa a combinação com
  `400 previous_interaction_id is not allowed when video task is set`. Por isso `extend` e
  `upscale` vão sem `video_config`: quem continua uma interação já carrega o contexto. Só as
  aberturas de cena (`text_to_video`, `image_to_video`, `reference_to_video`) mandam a task.
- **Upscale.** A documentação anuncia 1080p e 4K sem nomear uma task própria; aqui é um novo
  render da mesma interação pedindo a resolução maior, com a intenção no prompt.
