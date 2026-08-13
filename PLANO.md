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
| 8 — Automação sequencial | ⬜ | Fila de tarefas, encadeamento com resultados |
| 9 — Wake word própria | ⬜ | Treino de "James" via openWakeWord |
| 10 — Ferramentas maiores | ⬜ | Ver "Backlog" abaixo |

**428 testes automatizados.** Nada de Porcupine, Piper, whisper.cpp, Qt ou
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

## Fase 8 — Automação sequencial (próxima)

O que falta para "faça X, depois Y com o resultado".

**Problema a resolver.** Hoje o James executa as tools que o modelo pede num
turno, com teto de iterações, mas não há como um passo consumir a saída do
anterior nem retomar uma tarefa longa depois de reiniciar.

**Desenho proposto:**

```
james/agent/
  plan.py       Plano = lista ordenada de passos, cada um com
                {tool, args, guarda_resultado_como, usa_resultado_de}
  executor.py   Executa passo a passo. Cada passo passa pelo guard,
                igual a qualquer chamada. Resultado vai para um
                escopo nomeado que o próximo passo pode referenciar.
  queue.py      Fila persistida em disco: sobrevive a reinício do
                orquestrador (o watchdog já reinicia o processo 2).
```

**Decisões já tomadas:**

- Encadeamento por referência nomeada (`{{passo1.arquivo}}`), resolvida em
  Python antes do guard — nunca pelo modelo, que não pode injetar caminho.
- O guard avalia cada passo **no momento da execução**, com os argumentos já
  resolvidos. Aprovar um plano inteiro de antemão deixaria a substituição de
  variáveis fora da validação.
- Confirmação de Nível 2 acontece por passo, não uma vez pelo plano todo.
- Teto de passos e teto de replanejamento, como já existe para tools.

---

## Backlog de ferramentas

Agrupado por esforço real, não por ordem da lista original.

**Baixo esforço, base já existe**
- Organizar arquivos por data (`AAAA-MM`) — some ao módulo atual
- Processar arquivo por tipo (converter, resumir, extrair) — reusa visão e leitura
- Documento Word (`python-docx`) — irmão direto do módulo de Office

**Esforço médio**
- Automação sequencial (Fase 8, acima)
- Casa inteligente via Home Assistant — API REST local, chave no `.env`
- Celular via bot do Telegram — ponte de mão dupla, sem depender de UI
- Pesquisa aprofundada — várias buscas encadeadas com síntese; depende da Fase 8

**Esforço alto ou dependente de hardware**
- Múltiplos agentes — precisa da Fase 8 pronta primeiro; é orquestração de
  planos paralelos com um agregador
- Gestos por webcam — MediaPipe HandLandmarker; custa CPU continuamente
- Construtor de sites e de apps — geração de projeto inteiro; escopo grande
- Ferramenta de investidor — **atenção**: calcular risco e sugerir compra de
  ação é conselho financeiro. Dá para fazer a parte factual (cotação, histórico,
  indicadores via API pública) com honestidade; "vê se compensa comprar" é onde
  um modelo alucina com confiança e alguém perde dinheiro. Se entrar, entra
  como *dados e comparação*, com o veredito explicitamente fora do escopo.

---

## Riscos residuais

- Nada foi executado em hardware Windows real ainda. A Fase 0 é o primeiro
  passo obrigatório.
- A hipótese "esta CPU não tem AVX" continua não verificada. Se for falsa,
  faster-whisper e Silero VAD voltam a ser as melhores escolhas.
- Free tiers mudam de regra sem aviso; o modo degradado existe para isso.
- A latência depende da rede: dois provedores em sequência (percepção +
  raciocínio) significam dois ida-e-volta por turno.
