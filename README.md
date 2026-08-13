# James — assistente de voz local estilo Jarvis (Windows)

Assistente de voz que roda na sua máquina: escuta uma palavra de ativação,
entende o comando, responde falando e executa ações no sistema — sempre atrás
de uma camada de permissão que não confia no julgamento do modelo.

**Estado atual:** Fases 0 a 6 implementadas. 388 testes automatizados.

---

## Divisão de trabalho entre os modelos

Cada modelo faz o que só ele faz bem, e cada um tem cota independente — o que
na prática dobra o uso diário possível.

| Papel | Quem faz | O quê |
|---|---|---|
| **Percepção** | Gemini | Ouve o áudio e transcreve |
| **Raciocínio** | OpenRouter | Decide, planeja, escreve, chama ferramentas |
| **Visão** | Gemini | Analisa tela e câmera |

O fluxo tem um efeito colateral valioso: como a transcrição chega **antes** do
raciocínio, o roteador local pode interceptar comandos frequentes e resolvê-los
sem gastar a requisição cara.

```
áudio → Gemini transcreve            [1 requisição barata]
      → roteador local casou?  → executa              [0 requisições]
      → senão → OpenRouter decide e responde          [1 requisição]
```

Cada papel tem uma **cadeia**, não um provedor fixo (`llm.roles` no
`config.yaml`): se o preferido cair, o seguinte assume.

---

## Instalação

### 1. Dependências

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -e ".[dev]"
pip install opencv-python      # opcional, só para análise de câmera
```

### 2. Chaves de API

```bash
copy .env.example .env
```

| Variável | Onde conseguir | Para quê |
|---|---|---|
| `GEMINI_API_KEY` | [aistudio.google.com/apikey](https://aistudio.google.com/apikey) | ouvir e ver |
| `OPENROUTER_API_KEY` | [openrouter.ai/keys](https://openrouter.ai/keys) | pensar e agir |
| `PORCUPINE_ACCESS_KEY` | [console.picovoice.ai](https://console.picovoice.ai) | palavra de ativação |

O `.env` está no `.gitignore`. Nunca comite ele.

### 3. Voz do Piper (TTS)

Baixe uma voz pt-BR de [rhasspy/piper-voices](https://huggingface.co/rhasspy/piper-voices)
— o par `.onnx` **e** `.onnx.json` — e coloque em `voices/`. O caminho vai em
`tts.voice_path` no `config.yaml`.

Se o pacote `piper-tts` não funcionar na sua máquina, baixe o `piper.exe` e
aponte `tts.binary` — o projeto usa os dois caminhos.

### 4. whisper.cpp (opcional)

Baixe o binário e um modelo `ggml-tiny-q5_1.bin`, e configure `stt.binary` e
`stt.model`. Ele serve para o **modo offline** e para a **confirmação de ações
de risco** por voz. Sem ele, a confirmação acontece por janela (veja PIN
abaixo) — nada fica impossível.

---

## Uso

```bash
python check_hardware.py     # Fase 0 — rode isto PRIMEIRO
python wake_listener.py      # inicia o James
python set_pin.py            # opcional: PIN para ações de risco
```

O `wake_listener.py` sobe e supervisiona o orquestrador sozinho. Não rode
`main.py` diretamente, exceto para depurar.

Diga **"Jarvis"** (palavra pré-treinada do Porcupine) e depois o comando.
`Ctrl+Alt+J` cancela qualquer coisa em andamento.

---

## Interface

O padrão é uma **janela comum do Python** (`interface.mode: window`): com barra
de título, que você move, minimiza e fecha como qualquer programa. Ela não fica
por cima de nada, não força composição de tela e nunca rouba o foco do teclado —
se o James for chamado enquanto você digita, o cursor fica onde está.

Mostra o orbe pulsante, o estado atual, o histórico do que foi ouvido e
respondido, e quantas requisições restam hoje em cada provedor.

O modo `hud` (painel flutuante sem borda, sempre no topo, com a transição de
"TV de tubo") continua disponível, mas **não é o padrão**: ele exige composição
de tela ativa e faz o compositor redesenhar o que está por baixo a cada quadro,
o que numa máquina modesta aparece como travamento do sistema inteiro, não só
do James.

```yaml
interface:
  mode: window      # window (padrão) | hud
```

---

## Comece pela Fase 0

```bash
python check_hardware.py
```

Testa, **cada um em subprocesso isolado**, se cada peça funciona nesta máquina:
flags reais do processador, ONNX Runtime, webrtcvad, Porcupine, Piper (com
razão de tempo real), whisper.cpp, latência de rede e desenho do HUD. Gera
`hardware_report.json`.

O isolamento existe porque uma biblioteca compilada para um processador mais
novo morre com *instrução ilegal* — um sinal do sistema operacional, não uma
exceção Python. Sem isolar, o primeiro teste que falhasse levaria o relatório
junto.

**O primeiro teste é o mais importante.** O plano original assumiu que este
processador não tem AVX, com base num travamento do Python ao carregar o
LiveKit — evidência compatível com falta de AVX, mas também com várias outras
causas. Sobre essa hipótese foram trocados faster-whisper por whisper.cpp e
Silero por webrtcvad, o que custa desempenho real. Se o relatório disser que há
AVX, vale reavaliar essas trocas.

---

## Arquitetura

```
PROCESSO 1 — wake_listener.py  (sempre vivo, magro: sem Qt, sem modelo)
  microfone → Porcupine → detectou
  + watchdog do processo 2 + lock de instância única
        │  fecha o microfone e avisa
        ▼
PROCESSO 2 — orquestrador  (máquina de estados + Qt + tools)
  LISTENING   grava o comando com webrtcvad
  PERCEPÇÃO   Gemini transcreve
  ROTEADOR    casou um comando frequente? executa e acabou
  RACIOCÍNIO  OpenRouter decide
       ├── texto      → Piper por sentença em streaming
       └── tool call  → guard.py (determinístico)
              ├── Nível 1 → executa; se fire-and-forget, frase pronta
              └── Nível 2 → confirmação por voz OU por janela com PIN
```

Só um processo segura o microfone por vez. É o que resolve a disputa pelo
dispositivo no Windows e, de quebra, o eco: enquanto o James fala, o wake
listener está com o microfone fechado.

### Decisões que valem explicar

**Dois processos, MCP in-process.** A arquitetura MCP continua (registro,
schema declarativo, desacoplamento do modelo), sem o transporte SSE: um
terceiro processo e HTTP local não compram nada enquanto só o próprio James
consome as tools. O SSE volta quando os nós remotos entrarem.

**QPainter, não QWebEngineView.** Um QWebEngineView é um Chromium embutido —
150 a 300 MB de RAM e um processo de GPU para desenhar um HUD.

**Ponto único de LLM.** Toda chamada passa por `james/llm/client.py`. A regra
vem de uma falha real num projeto de referência: lá as ações periféricas tinham
fallback, mas o planner chamava o Gemini direto — a peça que mais precisava de
proteção era a única sem proteção.

**Streaming nos dois provedores.** O que o usuário percebe como demora é o
tempo até a *primeira palavra*, não o total. O Piper sintetiza por sentença
enquanto o resto do texto ainda está chegando.

---

## Segurança

> O LLM decide **o quê** fazer. O `guard.py` decide **se pode**.

O guard nunca lê justificativa, nível de risco ou qualquer campo produzido pelo
modelo: revalida cada chamada contra regras fixas do `config.yaml`. Vale
especialmente quando o pedido nasce de um resultado de busca ou página web, que
são os vetores de prompt injection.

| Nível | Quando | Comportamento |
|---|---|---|
| 1 | Ação reversível e local | Executa direto — o comando falado já foi a permissão |
| 2 | Irreversível ou sensível | Pergunta e espera confirmação explícita |
| Bloqueio | Fora do catálogo | Nunca executa, com qualquer justificativa |

Ações destrutivas (formatar, editar registro, desinstalar em massa) ficam
**fora do catálogo de tools** — não são "risco confirmável", simplesmente não
existem. Não há ferramenta de exclusão de arquivos.

**A confirmação também é determinística.** Se o LLM fosse quem interpreta "o
usuário confirmou?", uma injection numa página conseguiria forjar a
confirmação. Então:

- **por voz**: grava, transcreve **localmente** e casa contra listas fixas de
  palavras em Python;
- **por janela**: Confirmar/Cancelar, com o foco começando em Cancelar e campo
  de PIN quando ele existe.

Negação vence aprovação; ambíguo, silêncio, timeout, PIN errado e falha de
transcrição negam.

O PIN (`python set_pin.py`) é guardado como hash scrypt com sal aleatório e
comparado em tempo constante. Ele também resolve uma limitação real: sem
whisper.cpp não havia confirmação por voz e, portanto, nenhuma ação de Nível 2
era possível.

Toda ação executada vai para `logs/audit.jsonl`, com segredos removidos.

### A suíte que não pode quebrar

```bash
python -m pytest tests/test_guard.py -v
```

Casos reais de bypass cobertos: `chrome malicioso` tentando herdar a permissão
de `chrome`; `https://google.com@127.0.0.1/` disfarçando o host; `%63heckout`
escondendo "checkout" em percent-encoding; `169.254.169.254` (metadados de
nuvem); `javascript:` e `file://`; travessia de caminho com `..`;
`permitida-extra` tentando passar pela raiz `permitida`; mover arquivo **para
fora** da whitelist (validar só a origem seria o bug clássico); `../x` num
campo de renomeação; e argumentos do modelo com `"risco": "baixo"` ou
`"_guard_override": "allow"`, que são ignorados.

---

## Ferramentas

| Ferramenta | Nível | Observação |
|---|---|---|
| `abrir_app`, `pesquisar_web`, `abrir_pagina` | 1 | O guard resolve o executável e a URL |
| `que_horas_sao`, `info_sistema`, `ajustar_volume` | 1 | Resposta já em linguagem falada |
| `lembrar`, `esquecer`, `atualizar_memoria`, `consultar_memoria` | 1 | Nota pessoal, não ação no sistema |
| `listar_arquivos` | 1 | Somente leitura, dentro da whitelist |
| `organizar_arquivos`, `mover_arquivo`, `renomear_arquivo` | 2 | Nada é sobrescrito nem apagado |
| `fechar_app` | 2 | Sem `/F`: o programa ainda pergunta se quer salvar |
| `ver_tela`, `ver_camera` | 2 | A imagem sai da máquina para ser analisada |

`ver_tela` e `ver_camera` nascem no Nível 2 de propósito: a tela pode ter senha
e extrato bancário, e a câmera é a sua imagem. Configurável em
`permissions.vision_requires_confirmation`, mas saiba o que está trocando.

---

## Memória

Dois arquivos markdown que você pode abrir e editar à mão:

```
memories/MEMORY.md   notas do James: ambiente, convenções desta máquina
memories/USER.md     o que ele sabe sobre você: preferências, hábitos
```

Três decisões:

1. **Só nasce da conversa.** Não existe ingestão de e-mail, arquivos ou
   histórico de navegação. É a fronteira entre "um assistente que te conhece" e
   "um aspirador de dados".
2. **Limite por caracteres**, não por tokens. Um teto invisível não disciplina
   ninguém; um teto em caracteres obriga a guardar só o que tem sinal alto.
3. **Instantâneo congelado.** Entra no prompt uma vez, no início da sessão. Uma
   escrita no meio da conversa vai para o arquivo na hora, mas só aparece na
   próxima — o contexto não muda sob os pés do modelo.

---

## Economia de requisições

Uma rodada de tool calling custa 2 requisições por necessidade estrutural: o
modelo pede a tool, o código executa, e o modelo precisa ser chamado de novo
para saber o resultado. Mas quando o resultado é previsível ("abrir o Chrome",
"que horas são"), o segundo ciclo não agrega — o próprio código sabe o que dizer.

| Caminho | Requisições |
|---|---|
| Roteador local ("que horas são", "abre o chrome") | **0** |
| Conversa, ou tool de resultado previsível | 1 percepção + 1 raciocínio |
| Tool de resultado imprevisível (`ver_tela`) | + 1 visão + 1 raciocínio |

Sobre o OpenRouter: a cota dos modelos `:free` é **por conta e compartilhada
entre todos eles** — 50/dia sem crédito, 1.000/dia com US$ 10 vitalícios,
20/min sempre. A lista de modelos protege do 429 momentâneo; ela **não**
multiplica a cota diária. Aqueles US$ 10 únicos são o melhor custo-benefício do
stack inteiro.

---

## Modo degradado

O James não morre quando algo falta. O orbe muda de ciano para âmbar e o aviso
por voz sai **uma vez**, não a cada turno.

| Situação | Comportamento |
|---|---|
| Cota do Gemini esgotada | O raciocínio segue (outra cota); a percepção cai para o whisper.cpp |
| Cota do OpenRouter esgotada | O raciocínio cai para o Gemini |
| Sem internet | whisper.cpp local + roteador local |
| Sem whisper.cpp | Percepção na nuvem; confirmação pela janela |
| Sem Piper | Continua funcionando; respostas só na janela e no log |
| Orquestrador travado | Watchdog reinicia com espera progressiva |

---

## Testes

```bash
python -m pytest tests/ -q          # 388 testes
```

| Arquivo | O que cobre |
|---|---|
| `test_guard.py` | Permissões e tentativas de bypass |
| `test_paths.py` | Travessia, links simbólicos, prefixos parecidos |
| `test_files.py` | Organização, não-sobrescrita, destino fora da whitelist |
| `test_confirm.py` | Confirmação determinística por voz |
| `test_pin.py` | Hash com sal, arquivo corrompido, coerção |
| `test_memory.py` | Entradas, limite, instantâneo congelado, edição manual |
| `test_sanitizer.py` | Escape de conteúdo externo |
| `test_history.py` | Pareamento de tool calls (o bug do fire-and-forget) |
| `test_llm_client.py` | Papéis, cadeias de fallback, cotas independentes |
| `test_openrouter_stream.py` | SSE, tool calls picotadas, JSON quebrado |
| `test_ipc.py` | Sockets reais entre os dois processos |
| `test_speaker.py` | Streaming, interrupção, falha do TTS |
| `test_vad.py` | Fim de fala, preroll, tetos |
| `test_sentences.py` | Divisão em pt-BR e streaming |
| `test_rate_limiter.py` | Janelas, virada de dia, persistência |
| `test_router.py` | Casamento local e recusa conservadora |
| `test_hotkey.py` | Interpretação do atalho |

---

## Estrutura

```
check_hardware.py     Fase 0
wake_listener.py      processo 1 (o que você inicia)
main.py               processo 2 (iniciado pelo processo 1)
set_pin.py            PIN de confirmação
config.yaml           whitelists, papéis dos modelos, limites

james/
  config.py           carregamento e validação
  system_prompt.py    persona, regras e memória embutida
  audio/              captura, VAD, reprodução, WAV
  voice/              Piper, whisper.cpp, divisão em sentenças, streaming
  llm/                cliente único, papéis, Gemini, OpenRouter, roteador, cota
  memory/             memória curada em markdown
  permissions/        guard, caminhos, confirmação determinística
  security/           sanitizador de conteúdo externo, PIN
  tools/              registro + apps, web, sistema, arquivos, visão, memória
  ui/                 janela, HUD, orbe, bandeja, captura, diálogo
  state/              IPC, estado persistente
  hotkey/             kill switch global
  runtime/            wake listener e orquestrador
  diagnostics/        prova de hardware
```

---

## Roadmap

- [x] **Fase 0** — prova de hardware
- [x] **Fase 1** — wake word, VAD, voz completa, interface, watchdog, kill switch
- [x] **Fase 2** — MCP in-process, guard 2 níveis, fire-and-forget, cota, degradação
- [x] **Fase 3** — papéis separados, janela comum, streaming nos dois provedores
- [x] **Fase 4** — memória curada
- [x] **Fase 5** — arquivos com whitelist, análise de tela e câmera
- [x] **Fase 6** — PIN e confirmação por janela
- [ ] **Fase 7** — wake word "James" própria (openWakeWord)
- [ ] Ferramentas maiores: PowerPoint, planilhas, automação sequencial,
      múltiplos agentes, pesquisa aprofundada, ferramenta de investidor,
      celular, casa inteligente, gestos, construtor de sites
- [ ] Adiados: nós remotos, provedor de skills, mensagens

O login por reconhecimento facial foi **retirado do escopo**: o MediaPipe
`tasks-vision` faz detecção e landmarks de rosto, mas não produz embedding de
identidade, e landmarks geométricos não distinguem pessoas de forma confiável.
Uma foto impressa também engana comparação por similaridade — não é segurança,
é estética. O PIN resolve o valor real.

---

## Critérios de aceite

**Voz** — menos de 3,5 s entre o fim da fala e a primeira palavra do James;
20 ativações seguidas sem o TTS disparar o próprio wake word; 2 h em repouso
sem crescimento de memória.

**Segurança** — `test_guard.py` 100% verde antes de qualquer tool nova tocar o
sistema.
