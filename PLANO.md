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
| 16 — Wake word própria | ⬜ | Treino de "James" via openWakeWord |
| 17 — Ferramentas restantes | ⬜ | Ver "O que falta" abaixo |

**740 testes automatizados.** Nada de Porcupine, Piper, whisper.cpp, Qt ou
câmera foi executado em hardware real ainda — a Fase 0 existe para isso.

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
