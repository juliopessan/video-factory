# Clipper — design

Pacote `clipper/` do video-factory: pega a gravação longa de uma live (OBS) e devolve cortes verticais de 61–90 s, com legenda queimada, numa fila de aprovação local. Não toca no gerador de vídeo e não importa nada de `app/`: só segue as mesmas convenções (`VF_FFMPEG`/`VF_FFPROBE`, `VF_STORAGE_DIR`), para continuar funcionando quando o restante do repo mudar. A legenda queimada exige FFmpeg com libass (no Homebrew, `ffmpeg-full`).

## Por quê

Arthur joga GTA VI e transmite. O objetivo é monetizar no TikTok com uma conta nova no nome do Julio, usando cortes das **próprias** lives (conteúdo original). O Creator Rewards só paga vídeos com mais de 1 minuto, exige conta 18+, 10 mil seguidores e 100 mil views em 30 dias. Logo: volume e consistência de cortes bons de 60–90 s, com pouco trabalho manual por corte.

## Objetivos

- Arquivo longo entra, de 10 a 20 candidatos saem, ordenados por potencial.
- Cada corte tem de 61 a 90 s (nunca abaixo de 61), 1080x1920, legenda queimada com palavra ativa em destaque, áudio normalizado.
- Revisão em minutos: ver, aprovar/rejeitar, ajustar início/fim, editar título e hashtags.
- Roda 100% local. Sem custo por corte.

## Não-objetivos

- Postagem automática no TikTok (a API exige auditoria do app; fica para depois).
- Clipar lives de terceiros.
- Edição criativa (zoom, memes, música): o corte é fiel à live.

## Anti-objetivos (seria fracasso mesmo "funcionando")

- Cortes que começam ou terminam no meio da frase.
- Cortes de 58 s que não monetizam.
- Legenda por cima da interface do TikTok (rodapé e lateral direita).

## Abordagens consideradas

1. **Pipeline local em cima do video-factory** — escolhida pelo Julio. Mais rápido de entregar, custo zero, dá para evoluir.
2. Igual à 1 + postagem automática pela API do TikTok — depende de auditoria; adiada.
3. Plataforma web na nuvem com upload e cobrança — certa só se virar produto para outros streamers; exagero agora.

## Decisões de como construir (minhas, para o Julio revisar)

- Python, mesmo ambiente do repo. FFmpeg para tudo que é vídeo/áudio.
- Transcrição com `faster-whisper` (opcional, `pip install -r requirements-clipper.txt`), palavras com tempo. Também aceita `--transcript` com `.srt` ou `.json` já pronto.
- Pontuação sem LLM e sem custo: energia do áudio (FFmpeg `astats`, normalizada pela própria live) + léxico de reação/ação em PT-BR + exclamações + densidade de fala; penaliza trechos com muito silêncio. Janelas começam e terminam em limites de frase.
- Layout padrão: gameplay em largura total sobre fundo borrado, legenda abaixo, longe das zonas de interface do TikTok. Com `--facecam x:y:w:h`, a facecam entra abaixo do gameplay.
- Fila de revisão: FastAPI local (porta 8100) servindo uma página única; estado em `manifest.json` por job.
- Título e hashtags heurísticos (editáveis na revisão). Sem chamada de modelo por padrão.

## Fica para o construtor / próximos passos

- Re-ranking com LLM (título melhor, "gancho" nos 3 primeiros segundos).
- Detecção automática da facecam.
- Postagem via API do TikTok depois da auditoria.
