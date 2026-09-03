"""Como abrir um Chromium aqui, num lugar só.

O Playwright fixa um número de build e procura o navegador exatamente nele. Um
ambiente que já tem Chromium instalado por outro caminho — CI com cache,
imagem de container, `PLAYWRIGHT_BROWSERS_PATH` apontando para outro lugar —
falha com "Executable doesn't exist", que parece falta de instalação e não é.

O `try/except` que resolve isso estava copiado dentro de um teste. Copiado uma
vez é um detalhe; copiado em dois arquivos vira a versão que alguém esquece de
atualizar. Aqui é fixture, e todo teste de navegador usa a mesma.
"""

from __future__ import annotations

import os

import pytest

# Caminhos onde um Chromium pronto costuma estar quando o build fixado não bate.
_CANDIDATOS = (
    os.environ.get("JAMES_CHROMIUM"),
    "/opt/pw-browsers/chromium",
    "/usr/bin/chromium",
    "/usr/bin/chromium-browser",
    "/usr/bin/google-chrome",
)


def abrir_chromium(pw, **kwargs):
    """Lança um Chromium, tentando o do Playwright e depois os do sistema."""
    erros = []
    try:
        return pw.chromium.launch(**kwargs)
    except Exception as exc:  # noqa: BLE001
        erros.append(str(exc).splitlines()[0])

    for caminho in _CANDIDATOS:
        if not caminho or not os.path.exists(caminho):
            continue
        try:
            return pw.chromium.launch(executable_path=caminho, **kwargs)
        except Exception as exc:  # noqa: BLE001
            erros.append(f"{caminho}: {str(exc).splitlines()[0]}")

    pytest.skip("nenhum Chromium utilizável: " + " | ".join(erros))


@pytest.fixture(scope="session")
def navegador():
    """Um navegador para a sessão inteira — subir um por teste é caro."""
    sync_api = pytest.importorskip(
        "playwright.sync_api", reason="playwright não instalado"
    )
    pw = sync_api.sync_playwright().start()
    browser = abrir_chromium(pw)
    yield browser
    try:
        browser.close()
    finally:
        pw.stop()
