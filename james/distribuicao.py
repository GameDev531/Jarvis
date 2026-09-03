"""O que pode sair da máquina — decidido por ALLOWLIST, e conferido antes do ZIP.

## O problema

O ZIP distribuído continha artefatos de execução: bancos SQLite (`.db`, mais
os companheiros `-wal` e `-shm`), `runtime_state.json`, contadores de uso,
logs, `.jsonl` de auditoria e `__pycache__`. Nenhum deles é código; todos
contam algo sobre quem rodou o James.

## A causa raiz

O script antigo montava o pacote a partir de `git ls-files` e depois passava
um filtro de suspeitos por cima. Essa é a forma errada da pergunta:

    "esta árvore inteira, MENOS o que eu lembrar de excluir"

Um formato de arquivo novo — `.sqlite3`, `.pkl`, `state/perfis/` — entra por
padrão, e só sai se alguém lembrar de acrescentá-lo à lista. A lista de coisas
ruins é infinita; a de coisas boas é pequena e conhecida.

## A inversão

    "NADA, MAIS o que estiver explicitamente na allowlist"

`ALLOWLIST` diz quais arquivos da raiz e quais extensões dentro de quais
pastas fazem parte do projeto. O que não casa não entra — e não é preciso
prever o formato do artefato de amanhã.

## As três camadas

    1. allowlist   monta o candidato a pacote
    2. denylist    confere de novo, arquivo a arquivo (cinto e suspensório)
    3. scanner     lê o CONTEÚDO procurando padrão de segredo

Qualquer uma das três que reprove ABORTA a construção. Um ZIP que não sai é um
problema de dois minutos; um ZIP com a chave da API dentro é um problema que
não tem volta — a chave já circulou.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

# --------------------------------------------------------------- allowlist

# Arquivos da RAIZ que fazem parte do projeto. Nome exato, sem curinga: a raiz
# é justamente onde os artefatos de runtime aparecem (`hardware_report.json`,
# `.coverage`, `james.lock`), e um curinga aqui apagaria a proteção.
RAIZ_PERMITIDA = frozenset({
    ".env.example",
    ".gitignore",
    "PLANO.md",
    "README.md",
    "check_hardware.py",
    "check_modelos.py",
    "clonar_voz.py",
    "config.yaml",
    "distribuir.py",
    "main.py",
    "pyproject.toml",
    "set_pin.py",
    "wake_listener.py",
})

# Pasta -> extensões que podem sair dela. `""` cobre arquivo sem extensão.
PASTAS_PERMITIDAS: dict[str, frozenset[str]] = {
    "james": frozenset({".py"}),
    "tests": frozenset({".py", ".mjs", ".js", ".md"}),
    # `ui/web/vendor/` guarda o three.js sob licença MIT, e o `index.html`
    # importa dele: tirar a pasta do pacote quebraria a interface holográfica
    # de quem recebe. A trava aqui é a EXTENSÃO, não o nome da pasta.
    "ui": frozenset({
        ".html", ".css", ".js", ".mjs", ".md",
        ".svg", ".png", ".jpg", ".jpeg", ".webp", ".gif", ".ico",
    }),
    "skills": frozenset({".md", ".yaml", ".yml", ".txt"}),
    ".github": frozenset({".yml", ".yaml", ".md"}),
    "docs": frozenset({".md", ".png", ".svg"}),
}

# Pasta de runtime, e a ÂNCORA NA RAIZ NÃO É ENFEITE.
#
# `state/` e `logs/` são pastas de runtime na raiz — e são TAMBÉM pacotes
# Python: `james/state/` (IPC, runtime_state) e `james/logs/` (o logger e a
# privacidade). Um padrão sem âncora casa em qualquer profundidade e engole os
# dois: foi exatamente assim que um `state/` no `.gitignore` fez o pacote
# `james/state/` nunca chegar ao GitHub, com a suíte inteira passando porque
# os arquivos existiam no disco de quem escreveu. Ver
# tests/test_repo_integrity.py, que trancou aquele bug.
#
# Aqui o mesmo erro sairia como um ZIP que quebra com ModuleNotFoundError na
# primeira execução de quem recebeu.
PASTAS_DE_RUNTIME_NA_RAIZ = frozenset({
    "state",
    "logs",
    "memories",
    "models",
    "voices",
    "build",
    "dist",
    "htmlcov",
    ".venv",
    "venv",
})

# Estas, sim, em qualquer profundidade: nenhuma delas é nome de pacote Python
# do projeto, e todas guardam cache, sessão ou credencial.
PASTAS_PROIBIDAS = frozenset({
    "__pycache__",
    "browser_profiles",
    "cookies",
    "sessions",
    "credentials",
    ".git",
    ".pytest_cache",
    ".ruff_cache",
    "node_modules",
})

# Extensões que são dado de execução onde quer que estejam.
EXTENSOES_PROIBIDAS = frozenset({
    ".log", ".jsonl", ".db", ".sqlite", ".sqlite3",
    ".db-wal", ".db-shm", ".pyc", ".pyo", ".pem", ".key", ".pfx", ".p12",
    ".onnx", ".bin", ".ppn", ".task", ".wav", ".mp3", ".zip",
})

# Nome exato que nunca sai, mesmo rastreado pelo git. `Path(".coverage").suffix`
# é VAZIO — o ponto inicial faz o pathlib tratar o nome todo como stem, então
# filtrar por extensão deixaria este passar. Foi assim que ele entrou no
# repositório uma vez.
NOMES_PROIBIDOS = frozenset({
    ".env",
    ".coverage",
    "hardware_report.json",
    "runtime_state.json",
    "usage_counters.json",
    "james.lock",
    "credentials.json",
    "token.json",
    "pin.json",
})

# Nomes que denunciam artefato de runtime mesmo fora das pastas conhecidas —
# para o caso de alguém mudar `logs.dir` ou `memory.dir` no config.
PADRAO_RUNTIME = re.compile(
    r"(^usage_|^runtime_|\.db-(wal|shm)$|^audit\.|\.lock$)", re.IGNORECASE
)

# ...e a exceção que o padrão acima exige. `james/state/runtime_state.py` é
# CÓDIGO: o módulo que lê e escreve o `runtime_state.json`. O padrão olha o
# nome, e nome não distingue o dado do código que o manipula — a extensão
# distingue. Sem esta lista o pacote sairia sem um módulo importado no boot,
# quebrando com ModuleNotFoundError na casa de quem recebeu.
EXTENSOES_DE_CODIGO = frozenset({".py", ".js", ".mjs", ".html", ".css", ".md"})


# ------------------------------------------------------------ secret scanner

@dataclass(frozen=True)
class PadraoSegredo:
    nome: str
    regex: re.Pattern[str]


# Cada padrão é um formato de credencial de verdade, não uma heurística de
# entropia: falso positivo aqui ABORTA a construção, então precisa ser
# específico o bastante para não gritar com um hash de teste.
PADROES_DE_SEGREDO: tuple[PadraoSegredo, ...] = (
    PadraoSegredo("chave do Google/Gemini", re.compile(r"\bAIza[0-9A-Za-z_\-]{30,}")),
    PadraoSegredo("chave OpenAI/OpenRouter", re.compile(r"\bsk-[A-Za-z0-9_\-]{20,}")),
    PadraoSegredo("chave OpenRouter", re.compile(r"\bsk-or-v1-[A-Za-z0-9]{20,}\b")),
    PadraoSegredo("chave da Anthropic", re.compile(r"\bsk-ant-[A-Za-z0-9_\-]{20,}\b")),
    PadraoSegredo("token do GitHub", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}\b")),
    PadraoSegredo("chave da AWS", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    PadraoSegredo("token do Slack", re.compile(r"\bxox[abprs]-[A-Za-z0-9\-]{10,}\b")),
    PadraoSegredo("chave da ElevenLabs", re.compile(r"\bsk_[a-f0-9]{32,}\b")),
    PadraoSegredo("token do Home Assistant (JWT)",
                  re.compile(r"\beyJ[A-Za-z0-9_\-]{10,}\.eyJ[A-Za-z0-9_\-]{10,}\.")),
    PadraoSegredo("chave privada", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    # Atribuição direta no código: `OPENROUTER_API_KEY = "..."`. O que o
    # `.env.example` faz — nome sem valor — não casa.
    PadraoSegredo(
        "credencial embutida no código",
        re.compile(
            r"(?i)\b(api[_-]?key|access[_-]?token|secret|password|senha)\b\s*[:=]\s*"
            r"[\"'][^\"'\s]{16,}[\"']"
        ),
    ),
)

# Extensões que o scanner abre. Binário não é lido: não há regex útil ali e a
# leitura custa caro — a defesa contra binário é a allowlist, que não deixa
# nenhum entrar.
EXTENSOES_ESCANEADAS = frozenset({
    ".py", ".md", ".yaml", ".yml", ".json", ".txt", ".js", ".mjs",
    ".html", ".css", ".cfg", ".ini", ".toml", ".example", "",
})

MAX_BYTES_ESCANEADOS = 2 * 1024 * 1024

# A saída explícita, e a única. Uma linha marcada com isto é ignorada pelo
# scanner.
#
# Ela existe porque um scanner honesto ENCONTRA os próprios exemplos: o
# arquivo de teste que prova "isto aqui é reconhecido como chave da AWS"
# contém, necessariamente, algo com a cara de uma chave da AWS. Sem uma saída,
# as opções seriam mentir (montar a string por concatenação para escapar do
# regex) ou desligar a checagem naquele arquivo inteiro — e a segunda esconde
# o segredo de verdade que aparecer ali amanhã.
#
# Marcador por LINHA, e greppável: `grep -rn "pragma: exemplo-de-segredo"`
# mostra em segundos toda exceção que existe no projeto. Uma exceção que
# ninguém consegue listar não é exceção, é buraco.
MARCADOR_DE_EXEMPLO = "pragma: exemplo-de-segredo"


@dataclass
class Achado:
    caminho: Path
    motivo: str
    detalhe: str = ""

    def __str__(self) -> str:
        sufixo = f" — {self.detalhe}" if self.detalhe else ""
        return f"{self.caminho}: {self.motivo}{sufixo}"


@dataclass
class Pacote:
    """O resultado de montar (ainda sem escrever nada no disco)."""

    incluidos: list[Path] = field(default_factory=list)
    recusados: list[Achado] = field(default_factory=list)
    segredos: list[Achado] = field(default_factory=list)

    @property
    def pode_gerar(self) -> bool:
        """Fail-closed: qualquer segredo encontrado impede o ZIP.

        Recusas da allowlist NÃO impedem — elas são o funcionamento normal
        (o arquivo simplesmente fica de fora). Segredo dentro de um arquivo
        que a allowlist aprovou é outra coisa: significa que o pacote ia sair
        com credencial, e nenhum ZIP vale isso.
        """
        return not self.segredos


# ----------------------------------------------------------------- decisão

def motivo_de_recusa(relativo: Path) -> str | None:
    """Por que este caminho NÃO entra. `None` quando pode entrar.

    `relativo` é o caminho a partir da raiz do projeto, sempre.
    """
    partes = relativo.parts
    if not partes:
        return "caminho vazio"

    nome = partes[-1]
    if nome in NOMES_PROIBIDOS:
        return "arquivo de segredo ou de estado"

    pastas = [p.lower() for p in partes[:-1]]
    proibida = set(pastas) & PASTAS_PROIBIDAS
    if proibida:
        return f"pasta de cache ou sessão ({sorted(proibida)[0]}/)"
    if pastas and pastas[0] in PASTAS_DE_RUNTIME_NA_RAIZ:
        return f"pasta de runtime da raiz (/{pastas[0]}/)"

    sufixo = relativo.suffix.lower()
    if sufixo in EXTENSOES_PROIBIDAS:
        return f"dado de execução ({sufixo})"
    if sufixo not in EXTENSOES_DE_CODIGO and PADRAO_RUNTIME.search(nome):
        return "artefato de execução (nome)"

    if len(partes) == 1:
        if nome in RAIZ_PERMITIDA:
            return None
        return "não está na allowlist da raiz"

    topo = partes[0]
    permitidas = PASTAS_PERMITIDAS.get(topo)
    if permitidas is None:
        return f"pasta '{topo}/' não está na allowlist"
    if sufixo not in permitidas:
        return f"extensão '{sufixo or '(nenhuma)'}' não sai de {topo}/"
    return None


def varrer_segredos(caminho: Path, texto: str | None = None) -> list[Achado]:
    """Padrões de credencial dentro do CONTEÚDO de um arquivo."""
    if texto is None:
        if caminho.suffix.lower() not in EXTENSOES_ESCANEADAS:
            return []
        try:
            if caminho.stat().st_size > MAX_BYTES_ESCANEADOS:
                return [Achado(caminho, "grande demais para conferir")]
            texto = caminho.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            return [Achado(caminho, "não consegui ler para conferir", str(exc))]

    achados = []
    for numero, linha in enumerate(texto.splitlines(), start=1):
        if MARCADOR_DE_EXEMPLO in linha:
            continue
        for padrao in PADROES_DE_SEGREDO:
            if padrao.regex.search(linha):
                achados.append(Achado(caminho, padrao.nome, f"linha {numero}"))
    return achados


# ------------------------------------------------------------------ montagem

def _candidatos(raiz: Path) -> Iterable[Path]:
    """Percorre a árvore pela ALLOWLIST, sem entrar em pasta proibida.

    Não pergunta ao git: um arquivo novo e ainda não commitado é código do
    projeto, e o que o git ignora já é recusado pelas mesmas regras aqui.
    Isso também deixa a construção funcionar num diretório sem `.git` — quem
    recebeu o ZIP e quer repassá-lo.
    """
    for caminho in sorted(raiz.rglob("*")):
        if not caminho.is_file():
            continue
        relativo = caminho.relative_to(raiz)
        pastas = [p.lower() for p in relativo.parts[:-1]]
        # Poda barata, com a mesma âncora da decisão: `/logs/` fora,
        # `james/logs/` dentro.
        if set(pastas) & PASTAS_PROIBIDAS:
            continue
        if pastas and pastas[0] in PASTAS_DE_RUNTIME_NA_RAIZ:
            continue
        yield relativo


def montar(raiz: Path) -> Pacote:
    """Decide o pacote inteiro e confere o conteúdo. Não escreve nada."""
    pacote = Pacote()
    for relativo in _candidatos(raiz):
        motivo = motivo_de_recusa(relativo)
        if motivo:
            pacote.recusados.append(Achado(relativo, motivo))
            continue
        pacote.incluidos.append(relativo)
        pacote.segredos.extend(varrer_segredos(raiz / relativo))

    # O caminho absoluto vira relativo na mensagem: o achado é sobre o pacote,
    # não sobre a máquina de quem construiu.
    for achado in pacote.segredos:
        try:
            achado.caminho = achado.caminho.relative_to(raiz)
        except ValueError:
            pass
    return pacote
