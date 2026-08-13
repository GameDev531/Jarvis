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
| 12 — Wake word própria | ⬜ | Treino de "James" via openWakeWord |
| 13 — Ferramentas maiores | ⬜ | Ver "Backlog" abaixo |

**575 testes automatizados.** Nada de Porcupine, Piper, whisper.cpp, Qt ou
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
| ✋ Gestos | ⬜ | Custa CPU continuamente; avaliar depois da Fase 0 real |
| 🌐 Construtor de sites | ⬜ | Escopo grande |
| 📱 Construtor de apps | ⬜ | Escopo grande |
| 🤖 Múltiplos agentes | ⬜ | Base pronta (Fase 8) |
| 🔍 Pesquisa aprofundada | ⬜ | Falta busca que devolva conteúdo |

8 de 15 entregues.
