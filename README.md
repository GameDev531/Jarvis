# James — assistente de voz local estilo Jarvis (Windows)

Assistente de voz que roda na sua máquina: escuta uma palavra de ativação,
entende o comando, responde falando e executa ações no sistema — sempre atrás
de uma camada de permissão que não confia no julgamento do modelo.

**Estado atual:** Fases 0, 1 e 2 implementadas. 283 testes automatizados.

---

## Instalação

### 1. Dependências Python

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -e ".[dev]"
```

### 2. Chaves de API

```bash
copy .env.example .env
```

| Variável | Onde conseguir | Obrigatória |
|---|---|---|
| `GEMINI_API_KEY` | [aistudio.google.com/apikey](https://aistudio.google.com/apikey) | sim |
| `PORCUPINE_ACCESS_KEY` | [console.picovoice.ai](https://console.picovoice.ai) | sim |
| `OPENROUTER_API_KEY` | [openrouter.ai/keys](https://openrouter.ai/keys) | não (fallback) |

O `.env` está no `.gitignore`. Nunca comite ele.

### 3. Voz do Piper (TTS)

Baixe uma voz pt-BR de [rhasspy/piper-voices](https://huggingface.co/rhasspy/piper-voices)
— o par `.onnx` **e** `.onnx.json` — e coloque em `voices/`. O caminho vai em
`tts.voice_path` no `config.yaml`.

Se o pacote `piper-tts` não funcionar na sua máquina, baixe o `piper.exe` e
aponte `tts.binary` — o projeto usa os dois caminhos.

### 4. whisper.cpp (opcional, mas com consequências)

Baixe o binário e um modelo `ggml-tiny-q5_1.bin`, e configure `stt.binary` e
`stt.model`.

Sem ele o James ainda conversa (o áudio vai direto ao Gemini), mas perde duas
coisas: **modo offline** e **confirmação de ações de risco**. E sem poder
confirmar, toda ação de Nível 2 é recusada por padrão.

---

## Uso

```bash
python check_hardware.py     # Fase 0 — rode isto PRIMEIRO
python wake_listener.py      # inicia o James
```

O `wake_listener.py` sobe e supervisiona o orquestrador sozinho. Não rode
`main.py` diretamente, exceto para depurar.

Diga **"Jarvis"** (palavra pré-treinada do Porcupine) e depois o comando.
`Ctrl+Alt+J` cancela qualquer coisa em andamento.

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
  THINKING    manda o ÁUDIO direto ao Gemini            ← 1 requisição
       ├── texto      → SPEAKING, Piper por sentença em streaming
       └── tool call  → guard.py (determinístico)
                ├── Nível 1 → executa; se fire-and-forget, frase pronta
                │             sem voltar à API          ← ainda 1 requisição
                └── Nível 2 → CONFIRMING, transcrição LOCAL + lista fixa
                              de palavras, sem LLM
```

Só um processo segura o microfone por vez. É o que resolve a disputa pelo
dispositivo no Windows e, de quebra, o eco: enquanto o James fala, o wake
listener está com o microfone fechado.

### Decisões que valem explicar

**Áudio direto ao Gemini.** O modelo é multimodal e aceita o áudio bruto na
mesma requisição que devolve a resposta. Isso tira o STT do caminho crítico —
numa CPU lenta, transcrever localmente custa vários segundos — sem gastar
requisição extra. O contrapeso: no free tier o Google pode usar os dados para
treinar, e o áudio só é capturado depois da palavra de ativação, nunca em
streaming contínuo.

**Dois processos, MCP in-process.** A arquitetura MCP continua (registro,
schema declarativo, desacoplamento do modelo), sem o transporte SSE: um
terceiro processo e HTTP local não compram nada enquanto só o próprio James
consome as tools. O SSE volta quando os nós remotos entrarem.

**QPainter, não QWebEngineView.** Um QWebEngineView é um Chromium embutido —
150 a 300 MB de RAM e um processo de GPU para desenhar um HUD. O overlay atual
faz orbe, legenda e a transição de "TV de tubo" com QPainter puro.

**Ponto único de LLM.** Toda chamada passa por `james/llm/client.py`. A regra
vem de uma falha real num projeto de referência: lá as ações periféricas tinham
fallback, mas o planner chamava o Gemini direto — a peça que mais precisava de
proteção era a única sem proteção.

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
existem.

**A confirmação também é determinística.** Se o LLM fosse quem interpreta "o
usuário confirmou?", uma injection numa página conseguiria forjar a
confirmação. Então: grava, transcreve **localmente**, casa contra listas fixas
de palavras em Python. Negação vence aprovação; ambíguo, silêncio, timeout e
falha de transcrição negam.

Toda ação executada vai para `logs/audit.jsonl`, com segredos removidos.

### A suíte que não pode quebrar

```bash
python -m pytest tests/test_guard.py -v
```

Casos reais de bypass cobertos: `chrome malicioso` tentando herdar a permissão
de `chrome`; `https://google.com@127.0.0.1/` disfarçando o host; `%63heckout`
escondendo "checkout" em percent-encoding; `169.254.169.254` (metadados de
nuvem); `javascript:` e `file://`; travessia de caminho com `..`;
`permitida-extra` tentando passar pela raiz `permitida`; e argumentos do modelo
com `"risco": "baixo"` ou `"_guard_override": "allow"`, que são ignorados.

---

## Testes

```bash
python -m pytest tests/ -q          # 283 testes
```

| Arquivo | O que cobre |
|---|---|
| `test_guard.py` | Permissões e tentativas de bypass |
| `test_paths.py` | Travessia, links simbólicos, prefixos parecidos |
| `test_confirm.py` | Confirmação determinística |
| `test_sanitizer.py` | Escape de conteúdo externo |
| `test_history.py` | Pareamento de tool calls (o bug do fire-and-forget) |
| `test_ipc.py` | Sockets reais entre os dois processos |
| `test_speaker.py` | Streaming, interrupção, falha do TTS |
| `test_vad.py` | Fim de fala, preroll, tetos |
| `test_sentences.py` | Divisão em pt-BR e streaming |
| `test_rate_limiter.py` | Janelas, virada de dia, persistência |
| `test_router.py` | Casamento local e recusa conservadora |
| `test_hotkey.py` | Interpretação do atalho |

---

## Economia de requisições

Uma rodada de tool calling custa 2 requisições por necessidade estrutural: o
modelo pede a tool, o código executa, e o modelo precisa ser chamado de novo
para saber o resultado. Ele não pode saber o resultado antes de a tool rodar.

Mas quando o resultado é previsível ("abrir o Chrome", "que horas são"), o
segundo ciclo não agrega — o próprio código sabe o que dizer.

| Caminho | Requisições |
|---|---|
| Roteador local ("que horas são", "abre o chrome") | **0** |
| Conversa, ou tool de resultado previsível | **1** |
| Tool de resultado imprevisível (Fase 6) | 2 |

Sobre o OpenRouter: a cota dos modelos `:free` é **por conta e compartilhada
entre todos eles** — 50/dia sem crédito, 1.000/dia com US$ 10 vitalícios, 20/min
sempre. A lista de modelos protege do 429 momentâneo; ela **não** multiplica a
cota diária.

---

## Modo degradado

O James não morre quando algo falta. O HUD muda de ciano para âmbar e o aviso
por voz sai **uma vez**, não a cada turno.

| Situação | Comportamento |
|---|---|
| Cota do Gemini esgotada | Tenta OpenRouter; depois só roteador local |
| Sem internet | whisper.cpp local + roteador local |
| Sem whisper.cpp | Conversa normal, mas recusa toda ação de Nível 2 |
| Sem Piper | Continua funcionando; respostas só no overlay e no log |
| Orquestrador travado | Watchdog reinicia com espera progressiva |

---

## Estrutura

```
check_hardware.py     Fase 0
wake_listener.py      processo 1 (o que você inicia)
main.py               processo 2 (iniciado pelo processo 1)
config.yaml           whitelists, limites, o que é arriscado

james/
  config.py           carregamento e validação
  system_prompt.py    persona e as regras que vivem no prompt
  audio/              captura, VAD, reprodução, WAV
  voice/              Piper, whisper.cpp, divisão em sentenças, streaming
  llm/                cliente único, Gemini, OpenRouter, roteador, rate limiter
  permissions/        guard, caminhos, confirmação determinística
  security/           sanitizador de conteúdo externo
  tools/              registro + apps, web, sistema
  ui/                 overlay QPainter, bandeja, estados
  state/              IPC, estado persistente
  hotkey/             kill switch global
  runtime/            wake listener e orquestrador
  diagnostics/        prova de hardware
```

---

## Roadmap

- [x] **Fase 0** — prova de hardware
- [x] **Fase 1** — wake word, VAD, áudio→Gemini, Piper em streaming, overlay,
      half-duplex, kill switch, watchdog
- [x] **Fase 2** — MCP in-process, guard com 2 níveis, confirmação
      determinística, fire-and-forget, roteador local, rate limiter, fallback,
      modo degradado, sanitizador
- [ ] **Fase 3** — overlay completo (QWebEngineView, painéis, holograma)
- [ ] **Fase 4** — memória curada (`MEMORY.md`/`USER.md`)
- [ ] **Fase 5** — arquivos com whitelist, análise de tela
- [ ] **Fase 6** — PIN nas tools sensíveis
- [ ] **Fase 7** — wake word "James" própria (openWakeWord)
- [ ] Adiados: gestos por webcam, nós remotos, provedor de skills, mensagens

O login por reconhecimento facial foi **retirado do escopo**: o MediaPipe
`tasks-vision` faz detecção e landmarks de rosto, mas não produz embedding de
identidade, e landmarks geométricos não distinguem pessoas de forma confiável.
Uma foto impressa também engana comparação por similaridade — não é segurança,
é estética. PIN com hash local resolve o valor real.

---

## Critérios de aceite

**Fase 1** — menos de 3,5 s entre o fim da fala e a primeira palavra do James;
20 ativações seguidas sem o TTS disparar o próprio wake word; 2 h em repouso
sem crescimento de memória.

**Fase 2** — `test_guard.py` 100% verde; um dia inteiro de uso sem estourar
cota.
