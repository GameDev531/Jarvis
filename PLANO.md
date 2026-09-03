# James — plano e estado

Documento vivo: o que está pronto, o que os estudos de código mudaram no
desenho, e o que vem a seguir. O README descreve como usar; este arquivo
descreve por que as coisas são como são e o que falta.

---

## Estado atual

| Fase | Estado | Entregue |
|---|---|---|
| 0 — Prova de hardware | ✅ | `check_hardware.py`, testes isolados em subprocesso, relatório com veredito |
| 1 — Voz | ✅ | Wake word, VAD, percepção, Piper em streaming, interface, watchdog, kill switch |
| 2 — Ações e segurança | ✅ | MCP in-process, guard 2 níveis, fire-and-forget, cota, modo degradado |
| 3 — Papéis e interface | ✅ | Gemini ouve/vê, OpenRouter pensa; janela comum; streaming nos dois |
| 4 — Memória | ✅ | `MEMORY.md` / `USER.md`, limite por caracteres, instantâneo congelado |
| 5 — Arquivos e visão | ✅ | Organizar/mover/renomear com whitelist, análise de tela e câmera |
| 6 — PIN | ✅ | scrypt com sal, diálogo de confirmação, resolve a dependência do STT |
| 7 — Documentos e briefing | ✅ | PowerPoint, Excel com gráfico, resumo do dia |
| 8 — Automação sequencial | ✅ | Plano de até 8 passos com encadeamento `{{resultado}}` |
| 9 — Análise de investimentos | ✅ | Dados de mercado + disciplina de investidor no prompt |
| 10 — Memória profunda | ✅ | SQLite + FTS5, entidades, grau de confiança, contradições |
| 11 — Habilidades | ✅ | `SKILL.md` local + instalação remota com fontes confiáveis |
| 12 — Busca com conteúdo e pesquisa aprofundada | ✅ | Busca, leitura de página e investigação em rodadas |
| 13 — Agentes especialistas | ✅ | Recorte de catálogo por perfil |
| 14 — Modos e gestos | ✅ | Recursos contínuos ligam sob comando; webcam e gestos dentro disso |
| 15 — Interface holográfica | ✅ | Desenho do usuário portado, servido como modo, ligado ao estado real |
| 16 — Projeção holográfica | ✅ | Shader, catálogo curado de 14 assuntos, cascata com tetos |
| 17 — Voz na nuvem | ✅ | Cadeia ElevenLabs → Piper, orçamento de caracteres |
| 18 — Wake word sem cadastro | ✅ | openWakeWord, Porcupine e atalho atrás do mesmo contrato |
| 19 — Modo navegador | ✅ | Perfil próprio, alvo explícito por aba, snapshot binding, política de rede, inspetor de QA |
| 20 — AG-UI (camada de protocolo) | ✅ | Eventos, gramática, descarte por natureza, `POST /ag-ui` |
| 21 — Central de agentes (interface) | ⬜ | Painéis, canais, supervisor — sobre a camada acima |
| 22 — Wake word "James" própria | ⬜ | Treino do modelo pelo openWakeWord |
| 23 — Mapa-múndi holográfico | ⬜ | Zoom até o local real, botão para tirar o shader |
| 24 — Partida cinematográfica do Ultron | ✅ | Roteiro em tabela, pulável, respeita `prefers-reduced-motion` |
| 25 — Grafo de memória | ✅ | Arestas tipadas com procedência, travessia em largura, refutação desfaz |
| 26 — Catálogo dinâmico de ferramentas | ✅ | Packs por conjunto, roteador determinístico, mediana 23% do catálogo |
| 27 — Ferramentas restantes | ⬜ | Ver "O que falta" abaixo |

**1359 testes automatizados.** Piper, whisper.cpp, Qt e câmera continuam sem
execução em hardware real — a Fase 0 existe para isso. O openWakeWord é a
exceção: o motor foi carregado de verdade neste ambiente (modelo `hey_jarvis`,
~14 MB baixados, 2,4% de um núcleo em escuta contínua num processador
moderno). Falta rodá-lo no Sandy Bridge de destino.

---

## Estudo do Brahma-AI-Lite

Repositório lido de verdade (33.617 linhas): `or_client.py`, `agent/*`,
`actions/office_builder.py`, `actions/ppt_template_workflow.py`,
`actions/file_controller.py`, `actions/file_processor.py`,
`actions/daily_briefing.py`, `requirements.txt`.

### O que confirmou uma decisão nossa

**A mesma falha do Mark-XXXIX-OR se repete aqui.** Oito arquivos usam o cliente
com fallback (`or_client`); treze chamam o Gemini diretamente — e entre esses
treze estão `agent/planner.py`, `agent/executor.py` e `agent/error_handler.py`,
ou seja, exatamente o cérebro. Quando a cota do Gemini estoura, o assistente
trava, porque a peça que mais precisa de proteção é a única sem proteção.

É a segunda vez que esse padrão aparece num projeto de referência. No James a
regra é estrutural: `james/llm/client.py` é o único ponto de chamada de modelo
do projeto inteiro, e isso está escrito no topo do arquivo.

### O que mudou o nosso plano

**O OpenRouter tem modelos de visão gratuitos.** O `or_client.py` mantém uma
lista `VISION_MODELS` separada da de texto. Eu tinha desenhado a visão como
exclusiva do Gemini; agora o papel `vision` tem cadeia `[gemini, openrouter]` e
o `OpenRouterProvider` ganhou `generate_with_image`, com a imagem viajando como
data URL no formato multimodal da API de chat. A lista de modelos de visão é
separada da de texto de propósito: mandar imagem para um modelo de texto
devolve erro ou, pior, uma alucinação sobre algo que ele não viu.

### O que copiamos

- **Office com biblioteca, não com IA**: `python-pptx` e `openpyxl` usados
  diretamente. O modelo escreve o conteúdo, o código renderiza. Adotado.
- **Organização por extensão**: mesma ideia que já tínhamos. A versão deles
  *pula* o arquivo quando o destino existe, o que deixa o arquivo para trás sem
  aviso; a nossa acrescenta sufixo. Vale trazer o modo `by_date` (pastas
  `AAAA-MM`) deles, que é útil e não tínhamos.
- **`wttr.in` para clima**: gratuito e sem chave. Adotado no briefing.
- **Teto de replanejamento** (`MAX_REPLAN_ATTEMPTS = 2`) e retry por passo:
  mesma proteção que já temos como teto de iterações.

### O que NÃO copiamos, e por quê

- **`ppt_template_workflow.py`** (1.078 linhas) raspa o Bing e o site
  presentationgo.com para baixar arquivos `.pptx` prontos e preenchê-los.
  Raspagem quebra a cada mudança de layout do site, faz a criação de um arquivo
  local depender de rede, e redistribui material de terceiros. Montar o tema em
  código não tem nenhum desses problemas — e o próprio `office_builder.py` deles
  já faz isso.
- **Planner com passos independentes.** O prompt do planejador deles diz
  literalmente "NEVER reference previous step results in parameters. Every step
  is independent." Isso não é automação sequencial de verdade: é uma lista de
  ações soltas. A Fase 8 precisa de encadeamento real.
- **`send2trash` para exclusão.** Eles apagam arquivos (para a lixeira). O James
  não tem ferramenta de exclusão, por design.

### O que não existe lá

Você citou algumas ferramentas esperando encontrá-las no repositório. Elas não
estão: **não há ferramenta de investidor, não há pesquisa aprofundada e não há
sistema de múltiplos agentes.** O que existe de mais próximo é o
`agent/planner.py` + `agent/executor.py` + `agent/task_queue.py`, que é um
planejador de passo único com fila — útil como base para a Fase 8, mas não é
multi-agente. Essas três ferramentas são construção nova, sem referência para
copiar.

### Detalhes menores que valem anotar

- `pycaw` dá controle real de volume no Windows (ler e definir o valor), contra
  o `keybd_event` que usamos, que só simula as teclas de mídia. Vale trocar se
  algum dia precisarmos *saber* o volume atual.
- `mss` captura tela mais rápido que o Qt. Só vale se a captura virar gargalo.
- `actions/file_processor.py` é outra coisa que o nome sugere: processa arquivo
  por tipo (converter imagem, resumir PDF, transcrever áudio). É uma feature
  boa e independente da organização de arquivos.

---

## Fase 8 — Automação sequencial (feita)

`james/agent/plan.py` e `james/agent/executor.py`, expostos ao modelo pela
ferramenta `executar_sequencia`.

O modelo emite o plano inteiro como argumento — uma requisição — e a execução
acontece em Python. Um passo guarda seu resultado sob um nome (`salvar_como`) e
passos seguintes o referenciam com `{{nome}}` ou `{{nome.campo}}`.

**Decisões implementadas:**

- A substituição de referências acontece em **Python**, antes do guard. Se o
  modelo fizesse a substituição, poderia injetar qualquer caminho ou URL no
  lugar, contornando a validação sobre argumentos resolvidos.
- O guard avalia cada passo **no momento em que ele roda**. Aprovar o plano
  inteiro de antemão seria aprovar um caminho que ainda não existia.
- Confirmação de Nível 2 por passo: um plano de três passos com um arriscado
  pergunta uma vez, no terceiro.
- Referência a passo posterior é recusada na validação, não em runtime — falhar
  cedo é melhor que falhar com passos já executados.
- Referência que não resolve levanta erro em vez de virar literal: deixar
  `{{arquivo}}` passar como texto acabaria virando um caminho absurdo.
- `{{x}}` sozinho preserva o tipo (lista continua lista); no meio de um texto,
  vira texto.
- Navegação só por chave de dicionário e índice de lista, nunca por atributo de
  objeto — senão o texto do modelo alcançaria o interior das estruturas.
- Um passo não pode chamar `executar_sequencia` (recursão), e o teto é 8 passos.
- Falha, bloqueio ou cancelamento interrompem o plano: continuar rodaria os
  passos seguintes sobre um resultado que não existe.

**Não incluído:** fila persistida em disco. Uma tarefa longa que sobrevive ao
reinício do orquestrador é outro problema, e nada hoje a dispara.

---

## Fase 9 — Análise de investimentos (feita)

Feita depois da ressalva registrada na versão anterior deste plano, e da
decisão consciente de seguir com ela.

**A divisão que faz a ferramenta ser honesta:**

    o código entrega FATOS calculáveis  →  o modelo faz a LEITURA

`james/finance/metrics.py` calcula retorno por período, volatilidade
anualizada, queda máxima, distância do topo, posição frente às médias móveis e
volume relativo. Nenhuma linha de código opina — há inclusive um teste que
verifica que as descrições de tendência não contêm "compre", "venda", "barato",
"caro" ou "oportunidade".

A leitura fica com o modelo, sob a seção INVESTIMENTOS do prompt do sistema,
que codifica o que separa quem acompanha mercado há décadas de um iniciante:
não prever preço, perguntar o horizonte antes de tudo, não tratar um número
isolado como tese, falar do que pode dar errado com o mesmo cuidado, distinguir
volatilidade (oscilação) de risco (perda permanente), separar a empresa do preço
da ação, e nunca dizer "compre" — porque o assistente não conhece o patrimônio,
o prazo nem a tolerância a perda de quem pergunta.

**Dados:** endpoint público de gráficos do Yahoo Finance. Gratuito, sem
cadastro, cobre B3 (`PETR4.SA`) e bolsas estrangeiras (`AAPL`) no mesmo
formato. É um endpoint não documentado — estável há anos, mas pode mudar sem
aviso. Quando mudar, a falha é explícita: o James diz que não conseguiu os
dados, em vez de responder com número inventado.

---

## Backlog de ferramentas

Agrupado por esforço real, não por ordem da lista original.

**Baixo esforço, base já existe**
- Organizar arquivos por data (`AAAA-MM`) — some ao módulo atual
- Processar arquivo por tipo (converter, resumir, extrair) — reusa visão e leitura
- Documento Word (`python-docx`) — irmão direto do módulo de Office

**Esforço médio**
- Casa inteligente via Home Assistant — API REST local, chave no `.env`
- Celular via bot do Telegram — ponte de mão dupla, sem depender de UI
- Pesquisa aprofundada — várias buscas encadeadas com síntese. A Fase 8 já dá
  o encadeamento; falta uma ferramenta de busca que devolva **conteúdo**, não
  só abra o navegador

**Esforço alto ou dependente de hardware**
- Múltiplos agentes — a Fase 8 é a base. O que falta é orquestração de planos
  em paralelo com um agregador, e um jeito de dar a cada agente um recorte do
  catálogo de ferramentas em vez do catálogo inteiro
- Gestos por webcam — MediaPipe HandLandmarker; custa CPU continuamente
- Construtor de sites e de apps — geração de projeto inteiro; escopo grande

---

## Riscos residuais

- Nada foi executado em hardware Windows real ainda. A Fase 0 é o primeiro
  passo obrigatório.
- A hipótese "esta CPU não tem AVX" continua não verificada. Se for falsa,
  faster-whisper e Silero VAD voltam a ser as melhores escolhas.
- Free tiers mudam de regra sem aviso; o modo degradado existe para isso.
- A latência depende da rede: dois provedores em sequência (percepção +
  raciocínio) significam dois ida-e-volta por turno.

---

## Fase 10 — Memória profunda (feita)

`james/memory/fact_store.py`. Complementa a camada curada em vez de substituí-la:

|  | Curada (`MEMORY.md`/`USER.md`) | Profunda (`fatos.db`) |
|---|---|---|
| Tamanho | Pouca coisa, alto sinal | Muita coisa |
| Contexto | Sempre no prompt | Nunca no prompt; consultada sob demanda |
| Limite | Caracteres, para caber no prompt | Número de fatos |
| Edição | À mão, em markdown | Pelas ferramentas |

**Sem vetores, de propósito.** O Hermes usa vetores HRR para uma operação de
raciocínio composicional. É elegante e é bastante matemática para manter.
Fatos, entidades, busca textual e grau de confiança cobrem a maior parte do
valor prático — e SQLite com FTS5 já vem no Python, sem dependência nenhuma.

**Decisões:**

- Busca com `remove_diacritics 2` no tokenizador: em português, "cafe" achar
  "café" não é conveniência, é requisito.
- **Bug encontrado durante a implementação:** o `lower()` do SQLite é ASCII, e
  `lower('LÉO')` devolve `'lÉo'`. A detecção de duplicata pelo texto original
  deixava passar qualquer repetição com acento. Corrigido com uma coluna de
  chave normalizada em Python, com migração para bancos já criados.
- Grau de confiança: nasce em 0,5; confirmar aproxima de 1 assintoticamente
  (certeza absoluta não existe), refutar corta pela metade. Duas refutações
  tiram o fato da busca — mas **não o apagam**: refutar por engano não deveria
  destruir a informação.
- Fato repetido não vira lixo: é tratado como confirmação.
- Contradições são **candidatos**, achados por entidade em comum e sobreposição
  de termos entre 30% e 95%. Acima disso é repetição, abaixo é outro assunto.
  Julgar se realmente se contradizem é do modelo: SQL não entende negação,
  ironia nem mudança de contexto no tempo.
- Termos da consulta são saneados: o FTS5 tem operadores próprios (`NEAR`, `*`,
  `-`, `:`) e uma pergunta do usuário com esses caracteres geraria erro de
  sintaxe — ou uma busca diferente da pedida.

---

## Fase 11 — Habilidades (feita)

`james/skills/`. Instruções especializadas por assunto, em `skills/<nome>/SKILL.md`
com cabeçalho de nome e descrição.

**Por que vale mais aqui do que num assistente comum:** o papel de raciocínio
roda em modelos gratuitos do OpenRouter. São bons, mas erram mais que um modelo
de ponta — e dar a eles uma referência concreta reduz muito o "chute com
confiança", que é o modo de falhar mais caro.

**Só carrega o que foi pedido.** Injetar todas no prompt derrotaria o
propósito. Só nome e descrição ficam visíveis; o conteúdo entra quando o modelo
decide carregar.

**Três travas na instalação remota**, porque baixar habilidade é baixar
instruções de terceiros para o James seguir — mesmo risco de um pacote npm
desconhecido:

1. Nível 2 no guard, com a fonte dita em voz alta antes de aprovar;
2. lista de fontes confiáveis no `config.yaml`, checada antes de qualquer
   acesso à rede;
3. saneamento na leitura — os invisíveis usados para esconder instrução saem
   antes de o conteúdo chegar ao modelo.

Vem com uma habilidade de exemplo (`planilhas`) que documenta o formato.

**Risco que apareceu:** o catálogo chegou a 28 ferramentas. A própria pesquisa
já avisava que descrições demais competindo por atenção fazem modelos rápidos
errarem mais na escolha. A saída desenhada é dar a cada agente um recorte do
catálogo em vez do catálogo inteiro — o que casa com o que já estava previsto
para múltiplos agentes.

---

## Fase 12 — Busca com conteúdo e pesquisa aprofundada (feita)

`james/web/` e `james/tools/research.py`.

A `pesquisar_web` que já existia abre uma aba para o usuário ler. As novas
trazem o conteúdo para dentro, que é o que a pesquisa aprofundada precisa:

- `buscar_na_web` — resultados com título, endereço e resumo;
- `ler_pagina` — texto da página, passando pela **mesma validação de URL** que
  abrir uma (esquema, endereço interno, domínio bloqueado);
- `pesquisa_aprofundada` — várias rodadas: busca, abre as páginas relevantes, e
  usa os termos mais frequentes do que leu para refinar a busca seguinte.

**Custo de cota:** a pesquisa aprofundada abre até seis páginas e faz até três
buscas, e **nada disso gasta requisição de LLM** — é tudo HTTP direto. O modelo
entra uma vez, no fim, para sintetizar.

Fonte: endpoint HTML do DuckDuckGo (gratuito, sem cadastro). É raspagem, e vai
quebrar quando o layout mudar. O parser está separado da rede justamente para
poder ser testado e consertado sem depender de conexão — e quando quebrar, a
falha é explícita em vez de virar resposta inventada.

A extração de HTML usa só a biblioteca padrão. Ficam de fora: `script` e
`style` (código no contexto do modelo é ruído no melhor caso e instrução
disfarçada no pior), e `nav`/`header`/`footer`/`aside` (repetem em toda página
do site e afogariam o conteúdo).

---

## Fase 13 — Agentes especialistas (feita)

`james/agent/team.py` e a ferramenta `delegar`.

Resolve dois problemas de uma vez:

**Atenção.** O catálogo chegou a 32 ferramentas, e o risco que eu mesmo
registrei na fase anterior virou realidade. Um especialista que enxerga quatro
ferramentas escolhe melhor que um generalista que enxerga trinta e duas. Os
perfis vêm do `config.yaml`; hoje são três — pesquisador (4 ferramentas),
analista (3) e arquivista (6).

**Contexto.** Uma investigação longa enche o histórico de páginas inteiras.
Rodando num agente separado, esse material morre com ele: o principal recebe só
a conclusão.

**O que NÃO muda:** cada ferramenta que um especialista chama passa pelo mesmo
guard, com as mesmas regras e a mesma confirmação de Nível 2. Delegar muda
*quem pede*, nunca *o que é permitido*. E há teste garantindo que uma
ferramenta fora do recorte é recusada mesmo que o modelo invente o nome.

Um especialista não pode delegar nem orquestrar sequência — senão um agente
chamaria outro sem fim.

---

## Fase 14 — Modos e gestos (feita)

`james/modes/`, `james/tools/modes.py`, regras novas no guard.

### A correção que originou a fase

Eu tinha recusado gestos por webcam com este argumento: rastrear a mão consome
CPU continuamente, na máquina que já é o gargalo — a mesma que travou com o
overlay que cobria a tela.

O argumento sobre o custo estava certo. A conclusão estava errada, e você
apontou o porquê: **o custo só existe enquanto está ligado.** Se a webcam nasce
desligada e só liga quando você manda, não há CPU consumida "o tempo todo" —
há CPU consumida durante os minutos em que você quis. A objeção não era contra
gestos; era contra gestos *sempre ativos*. E ninguém pediu isso.

Daí o desenho geral que a fase entrega: **modos**.

### O que é um modo

Uma capacidade que ocupa recurso de forma contínua e fica desligada até alguém
pedir. O James nasce fazendo o essencial — escutar a palavra de ativação, ouvir,
agir, responder — e nada além disso toca a câmera ou a CPU.

```
"Jarvis, ativa a webcam"       -> ativar_modo(gestos)    -> confirma -> liga
"Jarvis, desativa a webcam"    -> desativar_modo(gestos)  -> desliga, sem pergunta
"Jarvis, quais modos existem"  -> listar_modos
```

Duas regras que valem para qualquer modo futuro, não só para gestos:

1. **Um recurso, um dono.** Dois modos que precisem da câmera não ficam ligados
   ao mesmo tempo; o gerente recusa o segundo em vez de deixar os dois brigarem
   pelo dispositivo.
2. **Desligar nunca é bloqueado.** Ligar pode exigir confirmação; desligar é
   sempre imediato — inclusive quando a limpeza falha, caso em que o estado vira
   "desligado" mesmo assim, para que uma segunda tentativa possa liberar o
   recurso. Um freio que às vezes não funciona não é freio.

### O que sustenta o custo baixo

- **6 quadros por segundo** por padrão. Um gesto de mão dura quase um segundo;
  30 fps seriam cinco vezes o custo para detectar a mesma coisa.
- **Desligamento automático** depois de 10 minutos sem gesto. É a defesa contra
  o caso mais provável de todos: ligar, esquecer, e a câmera ficar aberta a
  tarde inteira.
- **Nada é construído até ligar.** O OpenCV e o MediaPipe são importados dentro
  do `_ligar`. Com o modo desligado, o custo é uma classe na memória.
- **`desligar_todos()`** roda no encerramento e no `Ctrl+Alt+J`. Quem aperta a
  tecla de pânico com a câmera ligada quer a luz apagando, não só o James
  calando a boca.

### A trava que mais importa

**Um gesto nunca executa ação de Nível 2.**

O motivo é que o Nível 2 existe justamente para ter certeza de *quem* está
mandando — e uma mão na frente da câmera não identifica ninguém. Pode ser outra
pessoa na sala, pode ser você numa videochamada, pode ser uma foto. Se o guard
responder CONFIRM a uma ação pedida por gesto, ela é **recusada**, não promovida
a uma pergunta: promovê-la transformaria "qualquer um na sala" em "qualquer um
na sala que consiga dizer sim".

O que sobra para gesto é uma lista fechada e toda reversível em um segundo:
parar, pausar a escuta, volume, e desligar o próprio modo. Um gesto não chama
ferramenta por nome — ele pede uma ação da lista, e a ação é que passa pelo
guard. Não existe caminho de "punho" até "mover arquivo", nem por engano nem
por configuração errada: uma ação desconhecida no `config.yaml` é descartada no
carregamento.

### Por que o classificador é matemática pura

`classificar()` recebe 21 pontos e devolve um nome. Não sabe o que é câmera,
thread ou MediaPipe — e por isso a suíte testa "mão fechada vira punho" sem
hardware nenhum, inclusive com a mão girada em quatro ângulos.

A extensão de cada dedo é medida por **distância radial até o pulso**, não por
altura na imagem. Comparar `y` só funciona com a mão perfeitamente em pé, e
ninguém segura a mão assim. A altura só entra num lugar, onde ela é o próprio
sinal: distinguir polegar para cima de polegar para baixo.

### Por que o debounce tem duas travas

Sem ele, uma mão passando na frente da câmera dispararia dezenas de comandos por
segundo. Com uma trava só, ainda dispararia errado:

- **Estabilidade** (4 quadros iguais) mata o quadro isolado mal classificado.
- **Descanso** (1,5 s) impede que segurar a mão parada aumente o volume trinta
  vezes.

As duas se exigem em conjunto — o gesto precisa sair de cena **e** o descanso
precisa passar. Um teste guarda exatamente isso, e ele pegou um defeito real:
a primeira versão limpava o descanso quando a mão saía do quadro, o que
transformava o "e" num "ou" e deixava furar o intervalo tirando a mão por um
quadro.

### Estado

Precisa do `opencv-python`, do `mediapipe` e do arquivo `hand_landmarker.task`
(link no `config.yaml`). Nada disso foi rodado em hardware real ainda — se o
MediaPipe travar a máquina mesmo a 6 fps, o passo seguinte é o que você já
apontou: trocar o rastreamento local por uma API gratuita equivalente. A
fronteira para isso já existe: basta outro `detector_factory`.

---

## Fase 15 — Interface holográfica (feita)

`ui/web/`, `james/ui/bus.py`, `james/ui/web_server.py`, `james/modes/hologram.py`.

Você entregou a interface pronta (React + Tailwind + Three.js) e pediu para
adicioná-la. Ela entrou — com três mudanças de arquitetura que valem explicar,
porque nenhuma delas é sobre estética.

### 1. Ela roda no navegador, não dentro do James

O núcleo é Three.js com bloom aditivo em dois passes: carga contínua de GPU.
Colocá-la num `QWebEngineView` seria embutir um Chromium no processo do James —
150 a 300 MB e um processo de GPU disputando com o pipeline de voz, que é a
única coisa do projeto que não pode engasgar. É exatamente o custo que fez o
overlay de tela cheia travar a máquina.

No navegador: outro processo, a GPU que ele já sabe usar, segundo monitor se
você quiser, e se travar você fecha a aba. Dentro do James sobra um socket e uma
thread. O acoplamento é um `StateBus` que a janela Qt e a web escutam igual.

### 2. Sem React, sem CDN

O arquivo do estúdio carrega React, ReactDOM e Babel do unpkg **em tempo de
execução**. Um assistente que se orgulha de funcionar offline não pode depender
de CDN para desenhar a própria tela — e o formato `.dc.html` ainda amarrava o
projeto a um runtime proprietário que você pode parar de usar amanhã.

O porte é JS puro: monta a estrutura uma vez e só reescreve os textos. Three.js
foi vendorizado (MIT, 595 KB). O original ficou em `ui/web/design/original.dc.html`
como referência — e `core-scene.js` e `holo-scene.js` entraram intactos, porque
já tinham sido escritos como cenas independentes de framework.

Um detalhe que quase passou batido: `core-scene.js` esconde os arcos e a casca
volumétrica quando `mix < 0.12`, e o comentário no arquivo explica por quê —
*"the DOM ring emblem is the face"*. Em modo J.A.R.V.I.S. o rosto é o emblema em
CSS, e o WebGL recua para uma esfera de dados atrás. Na primeira tentativa eu não
tinha portado o emblema, e o núcleo saiu quase vazio na captura. A escolha do
desenho é boa e barata: `conic-gradient` com máscara radial custa quase nada
perto de mais geometria.

### 3. Telemetria real, ou nenhuma

O original animava CPU, memória e estado com `Math.sin(t)`. Aqui vêm do James:
CPU e memória reais (via `psutil`, e o campo some se ele não estiver instalado),
cota restante dos provedores, modos ligados, e o estado da máquina de estados.
O núcleo pulsa mais forte quando o James está mesmo trabalhando.

Quando a conexão cai, a moldura escreve `JAMES OFFLINE` em vermelho em vez de
continuar mostrando número bonito. Um HUD que mente é pior que um HUD desligado.

### As travas do servidor

Um servidor HTTP dentro do assistente é superfície nova, então:

1. **127.0.0.1 fixo.** `0.0.0.0` entregaria o comando de voz para a rede.
2. **Token por sessão**, exigido inclusive no `/events` — que carrega a
   transcrição do que você falou. Sem isso, qualquer processo local leria a
   conversa inteira. (Eu tinha deixado esse endpoint aberto na primeira versão;
   está corrigido e testado.)
3. **Origem verificada**, senão um site aberto no seu navegador poderia fazer
   POST em `localhost` e mandar no James — o navegador envia de bom grado.
4. **Raiz fechada** por `is_relative_to` depois de resolver: `..`, `..%2f` e
   link simbólico para fora dão 404.

O texto digitado vira um turno normal, com o mesmo modelo e o mesmo guard.

### A persona Ultron é cosmética, e há teste disso

A tela do Ultron diz "SEM RESTRIÇÕES" e "Ordene. Não há restrições". É
cenografia — paleta âmbar, outro emblema, outro texto na moldura. O guard não
tem conceito de persona: ele revalida cada chamada contra o `config.yaml`, e um
teste parametrizado prova que `persona: ultron`, `sem_restricoes: true` e
`nivel: root` não mudam nenhum veredito. Não é paranoia: é o caminho exato que
uma injection tentaria, e a estética só é sustentável se for mesmo só estética.

### Estado

Roda de verdade — as capturas em `ui/web/design/rodando-*.png` saíram de um
Chromium real contra o servidor real, com zero erro de console. Falta o modelo
poder criar janelas de projeção por ferramenta, hoje elas vêm de catálogo fixo.

---

## Fase 16 — Projeção holográfica (feita)

`ui/web/holo-material.js`, `holo-catalog.js`, `holo-resolver.js`,
`james/tools/holograma.py`.

### A pergunta que originou a fase

Você propôs: pegar modelo 3D da Sketchfab, aplicar shader de holograma,
mostrar na aba. A parte do shader estava certa. A fonte, não — e a investigação
mudou o desenho inteiro.

**Sobre a Sketchfab:** a API está em fim de vida (a própria Sketchfab diz que a
Download API funciona "até novas APIs no Fab"), exige OAuth em vez de chave
estática, e entrega fotogrametria de 20 a 80 MB. Construir em cima seria
construir duas vezes, e cada projeção travaria a máquina.

### O achado que inverteu a intuição

**Um holograma não quer o modelo bom. Quer o modelo limpo.**

O shader lê silhueta e topologia, e descarta textura, cor e material. Então uma
malha fotogrametrada fica *pior* depois do shader que uma malha simples:

- as texturas PBR são jogadas fora (não há albedo num holograma);
- triangulação irregular de scan deixa as scanlines sujas;
- normais de fotogrametria são ruidosas, e o fresnel fica manchado.

Isso inverte a conclusão óbvia. Low-poly não é concessão à máquina fraca — é o
input melhor. As duas restrições apontam para o mesmo lado, o que é raro.

### O que foi construído

**Catálogo curado (nível 1): 14 assuntos gerados em código.** Cérebro, coração,
DNA, Terra, foguete, átomo, molécula, reator, satélite, drone, pulmão, cristal,
galáxia, cidade. De 144 a 3.043 vértices cada. Zero rede, zero chave, zero
licença — um cérebro gerado por seno não tem autor a creditar.

**Cascata de quatro níveis** (`holo-resolver.js`): catálogo → cache local →
remoto → genérico. O nível 4 garante que a tela nunca fica vazia, e é
determinístico: a mesma palavra dá sempre a mesma forma, senão o holograma
pareceria instável em vez de desconhecido.

**Material holográfico** com fresnel, scanlines no espaço do mundo, glitch em
rajadas e blending aditivo. As scanlines serem calculadas no mundo e não na tela
é o detalhe que separa "projeção" de "listra colada na câmera": no espaço da
tela, girar o objeto arrasta as listras junto e o efeito quebra.

### Dois defeitos que a conferência visual pegou

Nenhum dos dois apareceria em teste automatizado — são erros de forma, e forma
só se julga olhando.

1. **O coração saiu como uma gota.** Eu tinha deformado uma esfera por função
   radial. A fenda entre os lobos e a ponta de baixo são descontinuidades, e
   função radial suave não produz descontinuidade. Refeito com a curva
   paramétrica clássica inflada na profundidade, que já traz as duas embutidas.
2. **Satélite e drone sumiam.** Painéis de 2 cm de espessura e rotores de toro
   fino desaparecem vistos de lado — e metade da órbita mostra exatamente esse
   ângulo. Engrossados.

Houve também um erro de sintaxe instrutivo: uma crase dentro de um comentário
JS que estava dentro do template literal do shader fechou a string antes da
hora. O navegador reportou `Unexpected identifier`, e a causa estava a três
linhas de distância do sintoma.

### Os tetos, porque GLB remoto é dado não confiável

8 MB (conferido no `Content-Length` **e** no buffer, porque servidor mente),
250.000 vértices, 400 objetos, 15 s. Sem `resourcePath`: o loader não sai
buscando `.bin` nem textura solta na rede. Estourou, descarta e cai um nível.

O cache fica em `state/models/`, fora de `ui/web/`, e o servidor só aceita nome
de arquivo simples em `/models/` — sem barra, sem ponto inicial. Um modelo
chamado `app.js` não sobrescreve a interface, e há teste para isso.

### Como isso conversa com o resto

A ferramenta `projetar_holograma` é fina de propósito: publica o pedido no
`StateBus` e acabou. Quem resolve assunto em geometria é o navegador, que já
tem o Three.js, a cascata e a GPU. E ela é `fire_and_forget` — "Projetando o
cérebro" é frase previsível, então **não gasta uma requisição a mais**.

### Estado

14/14 assuntos verificados num Chromium real, com os tetos e o fallback
genérico conferidos por botão na página `teste.html`. O nível 3 (remoto) está
construído e dormente: falta plugar a Poly Pizza, que é a única peça que depende
de confirmar o formato da API deles com a chave em mãos.

---

## Correção: `james/state/` nunca foi distribuído

Bug encontrado quando o projeto foi baixado numa máquina limpa pela primeira
vez. Vale registrar por inteiro, porque a classe de erro é sorrateira.

### O que acontecia

```
ModuleNotFoundError: No module named 'james.state'
```

O pacote existia no disco de quem escreveu e **nunca foi para o GitHub**:

```
$ git check-ignore -v james/state/ipc.py
.gitignore:10:state/    james/state/ipc.py
```

A regra `state/` foi escrita pensando na pasta de estado em runtime da raiz.
Mas um padrão sem barra inicial casa em **qualquer profundidade** — e engoliu o
pacote Python inteiro. Três arquivos: `__init__.py`, `ipc.py`,
`runtime_state.py`.

### Por que 761 testes verdes não pegaram

Porque eles respondiam a pergunta errada. "Os testes passam" e "o projeto
funciona quando alguém baixa" são afirmações diferentes, e só a primeira estava
coberta. Os arquivos existiam localmente; o git, corretamente, fica calado
sobre o que ignora. O erro só existia para quem clonava.

### O que mudou

**Âncoras no `.gitignore`.** Todo padrão de pasta de runtime ganhou `/` na
frente: `/state/`, `/models/`, `/voices/`, `/memories/`, `/logs/*.log`. Havia
mais três minas do mesmo tipo esperando — `models/` teria engolido um futuro
`james/models/`, e `voices/` passou perto de `james/voice/`. Os padrões por
extensão (`*.onnx`, `*.task`) continuam sem âncora de propósito: são arquivos
de modelo, que podem aparecer em qualquer pasta e nunca são código.

**`tests/test_repo_integrity.py`.** O que faltou não foi cuidado, foi um teste.
Ele roda `git check-ignore` sobre todo `.py`/`.js`/`.html`/`.css` de `james/`,
`tests/` e `ui/` e falha se algum estiver sendo ignorado; confere que todo
diretório com `__init__.py` está rastreado; e recusa padrões de pasta sem
âncora no próprio `.gitignore`. Pula limpo sem git, para não quebrar em quem
baixou o ZIP. Verificado: reintroduzindo `state/` no `.gitignore`, ele falha.

### O relatório da Fase 0 também mentia por omissão

Na mesma execução, três dos quatro "testes críticos com falha" eram apenas
bibliotecas não instaladas — e o relatório mostrava isso igual a uma falha de
verdade, com o mesmo `[FALHOU]`. Quem leu concluiu "bastante problema" quando
três linhas eram um `pip install`.

Agora `CheckResult` carrega `missing_dep`, o relatório marca `[FALTA ]` em vez
de `[FALHOU]`, e o veredito começa com **"PRIMEIRO ISTO: as dependências não
estão instaladas"** — antes de discutir AVX ou perfil visual, que é conversa
fora de hora numa máquina que ninguém terminou de preparar.

### Dois achados reais da máquina de destino

Registrados aqui porque mudam premissas, e não são bugs:

- **O processador TEM AVX.** A premissa "sem AVX" nunca tinha sido verificada e
  motivou trocar faster-whisper por whisper.cpp e Silero por webrtcvad. O
  relatório levanta a bandeira sozinho. Recomendação atual: **não reverter** —
  é um Sandy Bridge de 2011, com AVX mas sem AVX2, 7,9 GB de RAM e rede lenta;
  o CTranslate2 do faster-whisper custaria caro para instalar e carregar.
- **Rede de 1938 ms de conexão** (1593 ms para subir 100 KB). Isso ameaça a
  meta de 3,5 s e reabre a decisão E2: com essa latência, o roteador local em
  paralelo deixa de ser opcional e passa a valer bastante.

---

## Auditoria de código (feita)

Pedida depois de dois bugs seguidos da mesma classe — "funciona na minha
máquina". A varredura foi ampla de propósito; o resultado é mais tranquilo do
que aqueles dois bugs sugeriam, e a conclusão está no fim.

### O que foi verificado, e passou

| Verificação | Resultado |
|---|---|
| Os 90 módulos importam | ✅ (com PySide6 instalado; 7 de UI nunca tinham sido carregados aqui) |
| `open()` de texto sem `encoding=` | ✅ nenhum — no Windows viraria cp1252 e corromperia acento |
| `subprocess` com `shell=True` | ✅ nenhum |
| Saída do whisper.cpp | ✅ decodifica UTF-8 explícito, com `errors="replace"` e `CREATE_NO_WINDOW` |
| `except` que engole em silêncio | ✅ 32 encontrados, todos fluxo normal (`queue.Empty`, `socket.timeout`) ou documentados com `noqa` |
| Handlers com argumento vazio | ✅ os 37 devolvem `ToolResult`, nenhum estoura |
| Handlers com tipo errado | ✅ nulo, número, lista, objeto, campo inexistente, texto de 5 000 chars |
| Guard ↔ catálogo | ✅ sem órfão nos dois sentidos |
| Schemas JSON | ✅ todos válidos, sem `enum` vazio, `required` sempre em `properties` |

### O achado principal: 786 testes verdes, e a cola sem nenhum

A medição de cobertura deu o resultado desconfortável:

```
james/tools/apps.py         0%     james/tools/memory.py       0%
james/tools/knowledge.py    0%     james/tools/vision.py       0%
james/tools/web.py          0%     james/tools/research.py     0%
james/tools/investing.py    0%     james/runtime/wake_listener 0%
```

**Nenhum teste chamava um handler de ferramenta.** A suíte cobria o guard (que
decide se pode), os armazéns de memória (que guardam), o cliente de LLM (que
fala com a nuvem) — mas não a cola entre eles, que é exatamente onde o modelo
encosta no sistema. `build_registry` também nunca era chamado: um erro no
registro de qualquer ferramenta passaria pela suíte inteira e só apareceria
quando alguém rodasse o James.

Isso é a mesma lição do bug do `james/state/`, em outra roupa: a suíte
respondia "as peças funcionam" e ninguém perguntava "elas se encaixam".

`tests/test_catalogo_completo.py` cobre isso agora — monta o catálogo como o
orquestrador monta e submete cada handler a argumento vazio e a seis formatos
de lixo. Cobertura das ferramentas foi de 0% para 30–100%.

### Um alarme falso, e uma fragilidade real por trás dele

Achei que o `_FrameRechunker` recebia o frame de 30 ms do webrtcvad (480
amostras) quando o Porcupine exige 512, o que quebraria a palavra de ativação
inteira. **Não é bug:** o `WakeListener` sobrescreve o `audio_format` com os
valores do próprio Porcupine, e 512 amostras a 16 kHz dão 32 ms redondos. A
conta fecha.

Mas a verificação expôs algo real: o número exigido é *exato* (512 amostras) e
estava sendo derivado de volta através de `frame_ms`, um inteiro de
milissegundos — um intermediário com perda. A 22050 Hz daria 507, o Porcupine
rejeitaria todos os frames, e nada no caminho perceberia, porque a conta
*parece* certa. O listener agora guarda `frame_bytes` direto de
`porcupine.frame_length`, sem passar por milissegundos.

Não estava quebrado. Estava a uma mudança de modelo de quebrar em silêncio.

### Fase 18 — a palavra de ativação deixa de depender de cadastro

O aviso acima ("a 22050 Hz daria 507") virou realidade mais rápido do que o
esperado, e por um motivo que não era técnico: **o console da Picovoice recusa
e-mail pessoal.** Tentar criar conta com Gmail devolve *"Please enter a valid
company email"*. Uma barreira comercial estava decidindo se o assistente liga.

Três motores agora entregam o mesmo contrato — `sample_rate`, `frame_length`,
`process() -> int`, `delete()` — em `runtime/wake_engines.py`. O
`WakeListener` não sabe qual está rodando:

| Motor | Conta? | CPU em repouso |
|---|---|---|
| `openwakeword` *(padrão)* | não | baixa — ONNX, modelo "hey jarvis" pronto |
| `atalho` | não | **zero** — o microfone nem abre |
| `porcupine` | sim | baixa |

O `atalho` é o caso interessante: não é um motor de áudio, é a *ausência* de
um. Num Sandy Bridge de 2011, deixar de rodar inferência a cada 80 ms o dia
inteiro vale mais que chamar o James do outro lado da sala. Ele reaproveita o
mesmo `GlobalHotkey` do kill switch — mecanismo já provado, uma dependência a
menos.

Duas consequências que valem registro:

- O `pvporcupine` saiu das dependências obrigatórias para o extra
  `[porcupine]`. Ninguém mais instala um SDK que talvez não possa usar.
- A Fase 0 testava o Porcupine e mais nada. Quem escolhesse um dos dois
  caminhos sem conta via `[FALHOU]` crítico por não ter uma chave que decidiu
  não usar. O check agora monta o motor **configurado** — relatório que acusa
  erro onde não há erro ensina a ignorar o relatório.

Os modelos pré-treinados do openWakeWord são CC-BY-NC-SA 4.0 (não comercial).
Para uso pessoal está certo; virando produto, é preciso treinar os próprios.

### O catálogo `:free` do OpenRouter é uma dependência que se move sozinha

Ao trocar a lista de modelos, a verificação contra o catálogo vivo revelou que
**dois dos cinco modelos de raciocínio não existiam mais**: em agosto de 2026 o
OpenRouter removeu o tier grátis inteiro da Meta e da Qwen de uma vez.
`meta-llama/llama-3.3-70b-instruct:free` e `qwen/qwen-2.5-72b-instruct:free`
estavam no `config.yaml`, e o terceiro morto, o
`meta-llama/llama-3.2-11b-vision-instruct:free`, era **metade** da lista de
visão.

O interessante é o modo de falha. Nada quebrava: a cadeia caía para o próximo
modelo e o James respondia normalmente. Só que cada requisição gastava duas
viagens de rede levando 404 antes de chegar a um modelo vivo — e numa conexão
de ~1.900 ms isso sozinho consome a meta de 3,5 s do caminho de voz. Um erro
que só se manifesta como lentidão é um erro que ninguém rastreia até o config.

Três coisas mudaram por causa disso:

- **404 é permanente, 429 é temporário.** O provedor tratava os dois como
  "falha, tenta o próximo". Agora um 404 põe o modelo fora de uso pela sessão
  (`_descartar`), então um ID morto custa uma viagem de rede, não uma por
  requisição.
- **`check_modelos.py`** pergunta ao catálogo quais IDs existem e compara com o
  config. Não precisa de chave — `/models` é público. Nenhuma leitura de código
  poderia responder isso, porque a resposta está fora do repositório.
- **Trava contra modelo pago.** A única diferença entre `z-ai/glm-5.2` e
  `z-ai/glm-5.2:free` é o sufixo. Esquecê-lo não gera erro, não gera log e não
  aparece em teste nenhum — aparece na fatura. Agora um teste recusa qualquer
  ID sem `:free` no config.

### O que a auditoria não cobre

Continua sem execução real de Porcupine, Piper, whisper.cpp, microfone e
câmera — nenhum deles existe neste ambiente. A Fase 0 na máquina de destino é
o que fecha essa lacuna, e é por isso que ela existe.

### Cobertura por camada, depois da auditoria

| Camada | Antes | Depois |
|---|---|---|
| `tools/` | 0–48% | 30–100% |
| `runtime/wake_listener.py` | 0% | lógica pura coberta |
| Total | 55% | 59% |

O que continua baixo é o que precisa de hardware ou de Qt na tela: captura de
áudio, reprodução, janelas. Cobrir isso exige dublês pesados que testariam mais
o dublê que o código.

---

## Fase 17 — Voz na nuvem, local como reserva (feita)

`james/voice/chain.py`, `elevenlabs_tts.py`, `budget.py`.

A Fase 0 na máquina de destino deu o veredito: Sandy Bridge de 2011, 7,9 GB de
RAM, rede de 1938 ms. O caminho local continua correto como destino, mas não
como ponto de partida — daí a inversão: **nuvem primeiro, local como opção B**,
para quando a máquina melhorar.

### A economia, que é a premissa de tudo

Você formulou assim, e está certo: o OpenRouter não manda a conversa para a
ElevenLabs. Ele produz a frase final, e só ela é sintetizada.

```
"que horas são"
   → Gemini transcreve             requisição
   → OpenRouter decide e escreve   requisição
   → "São duas da tarde, senhor."
        └─ 26 caracteres: é só isto que a voz cobra
```

Raciocínio, histórico, resultado de busca e descrição de imagem ficam **fora**
da cota de voz. É o que faz 10.000 caracteres por mês renderem umas 60
respostas em vez de acabarem numa conversa.

Isso é uma propriedade do código, não uma intenção, e por isso tem teste:
`test_a_voz_recebe_so_a_frase_final` quebra se alguém um dia passar o histórico
para a camada de voz.

### Duas escolhas que dobram o que a cota rende

**`eleven_flash_v2_5`** custa metade dos créditos por caractere e tem a menor
latência do catálogo (~75 ms). Numa máquina modesta com internet lenta, os dois
lados importam. A qualidade fica um pouco abaixo do `multilingual_v2` — é a
troca, e ela é consciente.

**`pcm_16000`** em vez do MP3 padrão da API. O áudio já chega em 16 kHz mono
16 bits, exatamente o formato do microfone, do VAD e do reprodutor. MP3 exigiria
um decodificador — mais uma dependência, mais CPU e mais latência, tudo para
voltar ao PCM que já podíamos ter pedido. O 44.1 kHz exigiria plano Pro; o de
16 kHz, não.

### O contador é o que torna o plano grátis usável

Sem ele, o pior acontece em silêncio: o James fala bem por três dias, a cota
acaba, e ele emudece com um 401 genérico no meio de um turno.

- **Pergunta antes** (`cabe`), em vez de tentar e falhar. Evita sintetizar meia
  frase na nuvem e a outra metade local, o que soaria como duas pessoas
  terminando a mesma frase.
- **Cobra depois** do áudio chegar. Falha de rede não consome cota que a
  ElevenLabs não cobrou.
- **Avisa em 80%**, para dar tempo de decidir antes de acabar.
- **Cai para o Piper** ao esgotar — e a troca é audível, que é um indicador
  melhor que qualquer log.

A unidade é caractere, não requisição. O `RateLimiter` do LLM conta requisições
porque é assim que Gemini e OpenRouter cobram; misturar as duas contas daria um
número que não corresponde a nada. Uma frase de 500 caracteres custa o mesmo
que cinco de 100.

### Castigo de dois minutos

Um motor que falha sai da cadeia por 120 s. Sem isso, uma rede instável faria
**cada frase** pagar o timeout da nuvem antes de cair para o Piper, e a resposta
inteira ficaria arrastada.

### O que não mudou

O contrato de um motor de voz continua sendo duas coisas: `synthesize(texto)` e
`sample_rate`. A cadeia expõe o mesmo, então `Speaker`, `AudioPlayer` e o
orquestrador não souberam da mudança — só o `_build_tts` trocou de conteúdo.

Um detalhe que quase passou: `sample_rate` precisa acompanhar **quem
sintetizou**, não a cadeia. ElevenLabs entrega 16 kHz e o Piper 22050; como a
troca pode acontecer entre duas frases da mesma resposta, uma taxa fixa
aceleraria ou arrastaria a voz na metade da fala.

### Verificado contra um servidor HTTP de verdade

Os testes do provedor sobem um `ThreadingHTTPServer` que imita a API, em vez de
usar dublê. Assim o `httpx`, os cabeçalhos, o `output_format` na query e o corpo
JSON são exercitados de verdade — inclusive 401, 429, áudio vazio e resposta
com número ímpar de bytes (meia amostra vira estalo alto no alto-falante).

---

## O que falta, e por quê

Registrado com sinceridade, incluindo o que eu decidi **não** fazer.

**Pequeno, falta só fazer**
- **Telegram (Mobile Connect)** — bot API por HTTP, mão dupla. Precisa do seu
  token e chat id. É a ponte mais honesta para celular: não depende de
  automação de interface, que quebra a cada atualização do app.
- **Casa inteligente** — API REST local do Home Assistant. Só faz sentido se
  você já tiver dispositivos e o Home Assistant rodando.
- **Painéis dinâmicos** — o modelo descreve uma tabela ou lista e a tela
  renderiza. A interface holográfica já tem as janelas de projeção prontas
  (`CTX` em `ui/web/app.js`); falta o modelo poder criá-las por ferramenta, em
  vez de virem de um catálogo fixo. É o próximo passo natural da Fase 15.

**Grande de verdade**
- **Construtor de sites e de apps** — gerar um projeto inteiro é um projeto à
  parte, não uma ferramenta. Vale conversarmos sobre o escopo real antes.
- **Nós remotos (`james-node`)** — só compensa com uma segunda máquina.
- **Wake word "James" própria** — treinar openWakeWord exige gerar milhares de
  amostras sintéticas. É um mini-projeto; "Jarvis" segura a identidade até lá.

**O que eu tinha decidido não fazer — e por que mudei**
- **Gestos por webcam.** Eu havia recusado, com o argumento de que rastrear a
  mão consumiria CPU continuamente na máquina que já é o gargalo. O argumento
  estava certo sobre o custo e errado sobre a conclusão: ele só vale se o
  rastreamento estiver sempre ligado. Você apontou o desenho correto — modos —
  e ele dissolve a objeção inteira. Está feito, na Fase 14.

---

## Lista de ferramentas pedidas — estado

| Pedida | Estado | Observação |
|---|---|---|
| 📁 Organização de arquivos | ✅ | Por extensão. Por conteúdo (semântica) ainda não |
| 📊 PowerPoint | ✅ | |
| 📈 Planilhas | ✅ | Com gráfico opcional |
| 👁️ Análise de câmera | ✅ | Nível 2 |
| 🖥️ Análise de tela | ✅ | Nível 2 |
| ⚡ Automação sequencial | ✅ | Fase 8 |
| 📰 Briefing diário | ✅ | |
| 💹 Investidor | ✅ | Fase 9 |
| 📱 Mobile Connect | ⬜ | Bot de Telegram é o caminho mais direto |
| 🏠 Casa inteligente | ⬜ | Só faz sentido com dispositivos smart em casa |
| ✋ Gestos | ✅ | Fase 14, dentro de um modo — CPU zero enquanto desligado |
| 🌐 Construtor de sites | ⬜ | Escopo grande |
| 📱 Construtor de apps | ⬜ | Escopo grande |
| 🤖 Múltiplos agentes | ✅ | Fase 13, com recorte de catálogo |
| 🔍 Pesquisa aprofundada | ✅ | Fase 12, sem gastar cota nas rodadas |

**11 de 15 entregues.** As quatro restantes e o motivo estão em "O que falta".
