"""A sequência de despertar do Ultron, verificada junto com o resto da suíte.

O código é JavaScript e roda no navegador, então a verificação vive em
`tests/js/despertar_check.mjs` e usa o Node. Este arquivo só a puxa para
dentro do `pytest` — porque um teste que só roda quando alguém lembra de
rodar à mão é um teste que não existe.

Sem Node instalado o caso é PULADO, não quebrado: o Node não é dependência do
James, e quem só quer usar o assistente não deveria ver a suíte vermelha por
falta de uma ferramenta de desenvolvimento.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

VERIFICACAO = Path(__file__).parent / "js" / "despertar_check.mjs"


@pytest.mark.skipif(shutil.which("node") is None, reason="Node não está instalado")
def test_despertar_do_ultron():
    processo = subprocess.run(
        ["node", str(VERIFICACAO)],
        capture_output=True,
        text=True,
        timeout=60,
        cwd=VERIFICACAO.parent,
    )
    # A saída inteira vai junto no erro: cada caso já diz o que esperava, e
    # ler isso é mais rápido que reproduzir a animação na mão.
    assert processo.returncode == 0, (
        f"\n{processo.stdout}\n{processo.stderr}"
    )
