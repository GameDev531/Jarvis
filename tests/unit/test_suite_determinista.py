"""A suíte unitária não depende do mundo — e isto aqui é o que prova.

Um teste que alcança a internet reprova quando a internet cai. Quem vê isso
três vezes aprende a rodar a suíte ignorando o vermelho, e aí ela parou de
servir para qualquer coisa. Por isso a proteção é uma trava (`sem_rede`, em
tests/conftest.py) e não uma recomendação — e por isso a trava também tem
teste.
"""

from __future__ import annotations

import socket
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent.parent
TESTES = RAIZ / "tests"


# ------------------------------------------------------ a trava de soquete


def test_conexao_para_fora_e_recusada():
    with pytest.raises(RuntimeError, match="pytest.mark.network"):
        socket.create_connection(("93.184.216.34", 80), timeout=1)


def test_soquete_local_continua_permitido(tmp_path):
    """O IPC e a interface holográfica usam soquete local. Barrar quebraria
    testes legítimos por um motivo que não é o da trava."""
    servidor = socket.socket()
    servidor.bind(("127.0.0.1", 0))
    servidor.listen(1)
    try:
        cliente = socket.create_connection(servidor.getsockname(), timeout=2)
        cliente.close()
    finally:
        servidor.close()


# ------------------------------------------------------------ o dublê de DNS


def test_o_dns_falso_responde_o_que_o_teste_declarou(dns_falso):
    dns_falso("exemplo.com", ["203.0.113.7"])
    from james.security.enderecos import resolver

    assert resolver("exemplo.com") == ["203.0.113.7"]


def test_nome_nao_declarado_falha_em_vez_de_sair_para_a_rede(dns_falso):
    """O padrão fecha: esquecer de declarar é erro visível, não consulta real."""
    from james.security.enderecos import NaoResolveu, resolver

    with pytest.raises(NaoResolveu):
        resolver("nao-declarado.exemplo")


# --------------------------------------------- nenhum teste unitário sai fora


# As pastas cujos testes PODEM depender do mundo, e este arquivo — que cita os
# marcadores para poder procurá-los, e casaria consigo mesmo.
_ISENTOS = ("integration", "e2e")


def _arquivos_de_teste() -> list[Path]:
    return [
        caminho
        for caminho in TESTES.rglob("test_*.py")
        if "__pycache__" not in caminho.parts
        and not any(pasta in caminho.parts for pasta in _ISENTOS)
        and caminho != Path(__file__)
    ]


def test_nenhum_teste_fora_de_integration_pede_rede():
    """`@pytest.mark.network` desliga a trava. Só pode existir em integration/."""
    fora = [
        caminho.relative_to(RAIZ).as_posix()
        for caminho in _arquivos_de_teste()
        if "mark.network" in caminho.read_text(encoding="utf-8")
    ]
    assert not fora, f"marcador de rede fora de tests/integration/: {fora}"


def test_nenhum_teste_unitario_exige_chromium():
    """A CI unitária não roda `playwright install`; um teste de navegador
    escondido aqui a quebraria com uma mensagem que não explica nada."""
    fora = [
        caminho.relative_to(RAIZ).as_posix()
        for caminho in _arquivos_de_teste()
        if "sync_playwright" in caminho.read_text(encoding="utf-8")
    ]
    assert not fora, f"teste de navegador fora de tests/integration/: {fora}"


def test_um_importorskip_no_topo_do_modulo_nao_leva_teste_bom_junto():
    """A armadilha que estava em `tests/test_navegador.py`.

    Um `pytest.importorskip` em nível de módulo pula o ARQUIVO INTEIRO. Lá ele
    ficava no meio do arquivo, depois das travas que recusam campo de senha —
    e numa máquina sem playwright essas travas não rodavam, com a suíte verde.
    """
    for caminho in _arquivos_de_teste():
        texto = caminho.read_text(encoding="utf-8")
        if "importorskip" not in texto:
            continue
        linhas = texto.splitlines()
        primeiro_teste = next(
            (i for i, linha in enumerate(linhas) if linha.startswith("def test_")), len(linhas)
        )
        skip = next(i for i, linha in enumerate(linhas) if "importorskip" in linha)
        assert skip < primeiro_teste, (
            f"{caminho.relative_to(RAIZ)}: importorskip depois de um teste — ele pula "
            "os anteriores também. Mova o grupo que precisa da dependência para "
            "tests/integration/."
        )


def test_as_tres_suites_existem():
    for pasta in ("unit", "integration", "e2e"):
        assert (TESTES / pasta).is_dir(), pasta
    assert (TESTES / "README.md").exists()


def test_os_marcadores_estao_declarados():
    """Sem declaração + `--strict-markers`, um marcador com erro de digitação
    passaria calado — e o teste que ele deveria isolar rodaria na CI unitária."""
    texto = (RAIZ / "pyproject.toml").read_text(encoding="utf-8")
    assert "--strict-markers" in texto
    for marcador in ("network:", "browser:", "integration:", "e2e:"):
        assert marcador in texto, marcador
