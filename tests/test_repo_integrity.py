"""O repositório entrega tudo que precisa para rodar?

Este arquivo existe por causa de um bug real e caro: o `.gitignore` tinha
`state/` sem barra inicial, pensando na pasta de estado em runtime da raiz. Mas
um padrão sem âncora casa em qualquer profundidade, e ele engoliu o pacote
Python `james/state/` inteiro — que nunca foi para o GitHub.

A suíte inteira passava. Os arquivos existiam no disco de quem escreveu, e o
git nunca reclama de arquivo ignorado: silêncio é o comportamento correto dele.
O erro só aparecia para quem clonava, e aparecia como `ModuleNotFoundError` no
primeiro comando — sem nenhuma pista apontando para o `.gitignore`.

A lição é que "os testes passam" e "o projeto funciona quando baixado" são duas
afirmações diferentes, e só a primeira estava coberta. Estes testes cobrem a
segunda.

Eles pulam limpo quando não há git (alguém baixou o ZIP), porque aí a pergunta
não faz sentido e falhar seria ruído.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent

# Onde mora código que precisa ser distribuído. `ui/` entra porque a interface
# holográfica é servida de lá em runtime: um arquivo faltando ali quebra a tela
# sem quebrar teste nenhum.
PASTAS_DE_FONTE = ("james", "tests", "ui")
EXTENSOES = (".py", ".js", ".html", ".css")
IGNORAR_NO_CAMINHO = ("__pycache__", "/vendor/", "/design/", ".egg-info")


def _tem_git() -> bool:
    if not (RAIZ / ".git").exists():
        return False
    try:
        subprocess.run(
            ["git", "--version"], capture_output=True, check=True, timeout=10
        )
        return True
    except (OSError, subprocess.SubprocessError):
        return False


pytestmark = pytest.mark.skipif(
    not _tem_git(), reason="sem git: nada a verificar sobre distribuição"
)


def _git(*args: str) -> str:
    resultado = subprocess.run(
        ["git", *args], cwd=RAIZ, capture_output=True, text=True,
        encoding="utf-8", timeout=30
    )
    return resultado.stdout


def _fontes() -> list[Path]:
    encontrados: list[Path] = []
    for pasta in PASTAS_DE_FONTE:
        base = RAIZ / pasta
        if not base.is_dir():
            continue
        for caminho in base.rglob("*"):
            if not caminho.is_file() or caminho.suffix not in EXTENSOES:
                continue
            texto = caminho.as_posix()
            if any(parte in texto for parte in IGNORAR_NO_CAMINHO):
                continue
            encontrados.append(caminho)
    return encontrados


# --------------------------------------------------------------- o essencial


def test_nenhum_arquivo_de_codigo_esta_sendo_ignorado():
    """O teste que teria pego o bug do `james/state/`.

    `git check-ignore` recebe todos os caminhos de uma vez e devolve só os que
    casam com alguma regra. Para código-fonte, essa lista tem que sair vazia.
    """
    fontes = _fontes()
    assert fontes, "nenhum arquivo de código encontrado — o teste está cego"

    relativos = [c.relative_to(RAIZ).as_posix() for c in fontes]
    resultado = subprocess.run(
        ["git", "check-ignore", "--stdin"],
        cwd=RAIZ,
        input="\n".join(relativos),
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
    )
    ignorados = [linha for linha in resultado.stdout.splitlines() if linha.strip()]

    assert not ignorados, (
        "Estes arquivos de código estão sendo ignorados pelo .gitignore e não "
        "chegariam a quem clona o repositório:\n  "
        + "\n  ".join(ignorados)
        + "\n\nQuase sempre a causa é um padrão sem barra inicial, que casa em "
        "qualquer profundidade. Ancore-o na raiz (ex: `/state/`)."
    )


def test_todo_pacote_python_esta_rastreado():
    """Um `__init__.py` fora do git é um pacote que não existe para quem clona."""
    pacotes = sorted(
        p.parent.relative_to(RAIZ).as_posix()
        for p in (RAIZ / "james").rglob("__init__.py")
        if "__pycache__" not in p.as_posix()
    )
    assert pacotes, "nenhum pacote encontrado — o teste está cego"

    rastreados = set(_git("ls-files").splitlines())
    faltando = [
        pacote for pacote in pacotes if f"{pacote}/__init__.py" not in rastreados
    ]
    assert not faltando, (
        "Pacotes Python que existem no disco mas NÃO estão no git:\n  "
        + "\n  ".join(faltando)
        + "\n\nQuem clonar o repositório vai receber ModuleNotFoundError."
    )


def test_arquivos_de_entrada_estao_rastreados():
    """Os pontos de partida que o README manda rodar precisam existir."""
    rastreados = set(_git("ls-files").splitlines())
    entradas = [
        "wake_listener.py",
        "main.py",
        "check_hardware.py",
        "config.yaml",
        "pyproject.toml",
        ".env.example",
    ]
    faltando = [e for e in entradas if e not in rastreados]
    assert not faltando, f"arquivos de entrada fora do git: {faltando}"


def test_interface_web_esta_completa():
    """A interface é servida do disco: um arquivo fora do git quebra a tela.

    E quebraria de um jeito ruim — sem erro no Python, só um 404 no navegador
    e uma tela preta.
    """
    rastreados = set(_git("ls-files").splitlines())
    essenciais = [
        "ui/web/index.html",
        "ui/web/app.js",
        "ui/web/core-scene.js",
        "ui/web/holo-scene.js",
        "ui/web/holo-material.js",
        "ui/web/holo-catalog.js",
        "ui/web/holo-resolver.js",
        "ui/web/vendor/three.module.js",
        "ui/web/vendor/GLTFLoader.js",
    ]
    faltando = [e for e in essenciais if e not in rastreados]
    assert not faltando, f"arquivos da interface fora do git: {faltando}"


# ------------------------------------------------- o .gitignore em si


def test_padroes_de_runtime_estao_ancorados():
    """Nomes genéricos de pasta precisam de barra inicial.

    `state/` casa com `james/state/`. `models/` casaria com `james/models/`.
    `voices/` passou perto de `james/voice/`. São nomes que aparecem
    naturalmente dentro de um pacote, e é por isso que a âncora importa.
    """
    linhas = (RAIZ / ".gitignore").read_text(encoding="utf-8").splitlines()
    regras = [
        linha.strip()
        for linha in linhas
        if linha.strip() and not linha.strip().startswith("#")
    ]

    perigosos = {"state/", "models/", "voices/", "memories/", "logs/", "cache/"}
    sem_ancora = [regra for regra in regras if regra in perigosos]
    assert not sem_ancora, (
        f"padrões de pasta sem barra inicial no .gitignore: {sem_ancora}. "
        "Sem a âncora eles casam em qualquer profundidade e podem engolir "
        "código-fonte (foi o que aconteceu com james/state/)."
    )


def test_segredos_continuam_ignorados():
    """A âncora não pode ter afrouxado o que realmente precisa ficar de fora."""
    for caminho in (".env", "state/fatos.db", "memories/USER.md", "voices/x.onnx"):
        resultado = subprocess.run(
            ["git", "check-ignore", "-q", caminho],
            cwd=RAIZ,
            capture_output=True,
            timeout=10,
        )
        assert resultado.returncode == 0, f"{caminho} DEVERIA estar ignorado"


# --------------------------------------------------- pontos de entrada

def test_main_aceita_as_flags_documentadas():
    """`--holograma` desfaz um nó: a interface web só ligava por voz, mas o
    campo de comando que ligaria o resto vive dentro dela. Se a flag sumir,
    volta a não haver como ver a tela sem microfone e chave."""
    import argparse
    from unittest.mock import patch

    import james.runtime.orchestrator as orq

    capturado = {}

    def falso_parse(self, args=None, namespace=None):
        ns = argparse.Namespace(modo=[], holograma=False)
        capturado["args"] = list(args or [])
        raise SystemExit(0)      # para antes de construir o orquestrador

    with patch.object(argparse.ArgumentParser, "parse_args", falso_parse):
        for flags in (["--holograma"], ["--modo", "gestos"], []):
            try:
                orq.main(flags)
            except SystemExit:
                pass
            assert capturado["args"] == flags
