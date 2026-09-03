"""O que só a rede de verdade responde. Marcado, e fora da CI unitária.

Estes testes existem porque um dublê de DNS prova que o CÓDIGO está certo, não
que o mundo se comporta como o dublê diz. As duas perguntas são legítimas; o
erro era misturá-las no mesmo arquivo, e deixar a segunda reprovar quando a
internet caía.

    pytest -m network        roda só estes
    pytest -m "not network"  é o que a CI unitária roda
"""

from __future__ import annotations

import socket

import pytest

pytestmark = [pytest.mark.network, pytest.mark.integration]


def _tem_dns() -> bool:
    try:
        socket.getaddrinfo("example.com", None)
        return True
    except OSError:
        return False


@pytest.mark.skipif(not _tem_dns(), reason="sem DNS nesta máquina")
def test_um_nome_publico_de_verdade_resolve_para_fora():
    """A contraparte real de `test_host_publico_passa`.

    Lá o resolvedor é um dublê e o que se prova é a lógica. Aqui se prova que
    a lógica combina com a internet — e o teste PULA quando não há rede, em
    vez de reprovar.
    """
    from james.security.enderecos import validar_host

    validar_host("example.com")


@pytest.mark.skipif(not _tem_dns(), reason="sem DNS nesta máquina")
def test_localhost_continua_barrado_com_o_resolvedor_real():
    """Sem dublê: o `localhost` da máquina de verdade aponta para dentro."""
    from james.security.enderecos import EnderecoBloqueado, validar_host

    with pytest.raises(EnderecoBloqueado):
        validar_host("localhost")


def test_a_marca_network_libera_o_soquete():
    """Prova que `sem_rede` respeita o marcador — senão nada aqui rodaria."""
    import socket as _socket

    assert _socket.socket.connect.__name__ != "_recusar"
