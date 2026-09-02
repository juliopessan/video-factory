# tools_tokens

Contexto citado que respeita a janela. Sem rede, sem embeddings, sem dependência
externa — só a biblioteca padrão.

```python
from tools_tokens import Headroom, index_path, pack_query

headroom = Headroom(window=200_000, reserve_output=8_000)
headroom.spend("conversation", 120_000)

index = index_path("./app")                       # walk, chunk, index
context = pack_query(
    "where is the retry backoff configured?",
    index, budget=6_000, headroom=headroom,
)

print(context.text)        # cited, budget-respecting context
print(context.manifest())  # and the full account of how it got there
```

## O que cada parte faz

**`Headroom`** é a conta viva da janela: quanto ela tem, quanto a saída reserva,
quanto cada parte já gastou. `spend()` recusa o que não cabe em vez de estourar
em silêncio; `allow(budget)` recorta um orçamento pedido ao que sobrou.

**`index_path`** percorre o diretório, ignora o que não interessa
(`node_modules`, `.git`, `storage`, binário, arquivo grande demais) e corta cada
arquivo em trechos **na estrutura** — `def`, `class`, `export`, cabeçalho
markdown, regra CSS — porque trecho que começa no meio de uma função não serve
de citação. Cada trecho guarda caminho, intervalo de linhas e o símbolo que
define.

**`pack_query`** busca com BM25 sobre identificadores, enche o orçamento em
ordem de relevância e devolve o texto com uma citação por bloco. Três regras
governam o que entra:

| Regra | Por quê |
|---|---|
| piso de relevância (45% do topo) | orçamento sobrando não é motivo para incluir trecho fraco — contexto é resposta, não é tudo que coube |
| no máximo 3 trechos por arquivo | um arquivo verboso não pode ocupar a janela inteira |
| orçamento verificado bloco a bloco | o corte acontece antes de montar, não depois |

**`manifest()`** é a conta inteira: termos da pergunta, termos que entraram por
expansão, orçamento pedido/efetivo/usado, cada bloco com sua pontuação e custo,
tudo o que ficou de fora **com o motivo**, estatísticas do índice e a janela
antes e depois.

## Duas decisões que valem explicação

**Expansão de vocabulário.** Quem pergunta por "retry backoff" procura um código
que se chama `POLL_INTERVAL_SECONDS`. `query.py` tem uma tabela pequena e
explícita de sinônimos de desenvolvimento; termos vindos dela pesam 0,55 contra
1,0 dos termos originais, e o manifesto mostra quais foram. É tabela, não
modelo: você lê, discorda e edita.

**Contagem estimada.** Sem o tokenizador do provedor, a contagem é uma
heurística local (~4 caracteres por token, com identificadores longos custando
mais). O manifesto declara isso em `counting`. Para contagem exata:

```python
from tools_tokens import set_counter
set_counter(lambda texto: meu_tokenizador.encode(texto).__len__())
```

## Métricas medidas

Cinco consultas contra `./app` deste repositório, com a janela do exemplo
(200.000, reserva de 8.000, conversa já ocupando 120.000 — sobram 72.000):

```
ÍNDICE  15 arquivos · 317 trechos · 61.282 tokens · 2.849 termos · 288 ms

consulta                                         blocos  tokens   uso  fora     ms
where is the retry backoff configured?               10    2881   48%    50    2.0
how are subtitles burned into the video?              8    2831   47%    52    2.0
onde o provider decide entre gemini e mock?           7    1892   32%    48    1.0
how is the video export budget or cost computed?     10    2978   50%    50    2.0
where does the pipeline extract the last frame?       5    1626   27%    55    1.0

mediana: 2.831 tokens de 6.000 pedidos · 2 ms por consulta
```

O contexto mediano é **4,6% do repositório indexado** — 2.831 tokens no lugar de
61.282. O índice é construído uma vez (288 ms) e cada consulta custa 2 ms.

A coluna `fora` conta o que o manifesto explica: trecho abaixo do piso de
relevância ou além do limite de três por arquivo. O orçamento sobra de propósito
nas consultas específicas: encher 6.000 tokens quando 1.626 respondem seria
gastar janela para piorar o sinal.

## Testes

```bash
python3 tests_tools_tokens.py    # 35 checagens, offline
```
