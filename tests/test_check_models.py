"""O catálogo :free do OpenRouter muda sozinho, e o config não fica sabendo.

Em agosto de 2026 o OpenRouter removeu o tier grátis inteiro da Meta e da Qwen.
O `config.yaml` do James continuou apontando para dois modelos mortos, e o modo
como isso falhava é o pior possível: **nada quebrava**. Cada requisição gastava
duas viagens de rede levando 404 antes de chegar a um modelo vivo. O James
ficava lento, e o config era o último lugar onde alguém iria procurar.
"""

from __future__ import annotations

import pytest

from james.config import Config
from james.diagnostics.check_models import (
    CatalogoIndisponivel,
    _veredito,
    buscar_catalogo,
    conferir,
    modelos_configurados,
)


def config_com(models=(), vision=()):
    return Config({"llm": {"openrouter": {
        "models": list(models), "vision_models": list(vision),
    }}})


class _RespostaFalsa:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


@pytest.fixture
def httpx_falso(monkeypatch):
    """Troca só o `get` do httpx — o resto do módulo continua real."""
    import httpx

    estado = {"payload": {"data": [{"id": "a:free"}, {"id": "b:free"}]}, "status": 200}

    def get(url, timeout=None):
        if isinstance(estado["payload"], Exception):
            raise estado["payload"]
        return _RespostaFalsa(estado["payload"], estado["status"])

    monkeypatch.setattr(httpx, "get", get)
    return estado


# ------------------------------------------------------------- o catálogo


def test_le_os_ids_do_catalogo(httpx_falso):
    assert buscar_catalogo() == {"a:free", "b:free"}


def test_rede_fora_nao_vira_veredito_de_modelo_morto(httpx_falso):
    """Não conseguir perguntar é diferente de a resposta ser "não existe".

    Confundir os dois faria alguém apagar uma lista boa por causa de um cabo
    de rede solto.
    """
    httpx_falso["payload"] = RuntimeError("conexão recusada")
    with pytest.raises(CatalogoIndisponivel):
        buscar_catalogo()


def test_resposta_em_formato_estranho_e_recusada(httpx_falso):
    """Aceitar um formato inesperado daria um catálogo vazio — e um catálogo
    vazio faz TODO modelo parecer morto."""
    httpx_falso["payload"] = {"modelos": ["a:free"]}
    with pytest.raises(CatalogoIndisponivel, match="formato inesperado"):
        buscar_catalogo()


def test_catalogo_vazio_e_recusado(httpx_falso):
    httpx_falso["payload"] = {"data": []}
    with pytest.raises(CatalogoIndisponivel, match="vazio"):
        buscar_catalogo()


# ------------------------------------------------------------ a conferência


def test_separa_vivos_de_mortos():
    config = config_com(models=["vivo:free", "morto:free"], vision=["vivo:free"])
    resultado = conferir(config, {"vivo:free"})
    assert resultado["raciocínio"] == [("vivo:free", True), ("morto:free", False)]
    assert resultado["visão"] == [("vivo:free", True)]


def test_a_ordem_do_config_e_preservada():
    """A ordem é a preferência do provedor; um relatório que a embaralha
    esconde qual modelo está sendo tentado primeiro."""
    ids = [f"m{n}:free" for n in range(6)]
    resultado = conferir(config_com(models=ids), set(ids))
    assert [modelo for modelo, _ in resultado["raciocínio"]] == ids


def test_config_sem_openrouter_nao_explode():
    assert conferir(Config({}), {"a:free"}) == {"raciocínio": [], "visão": []}


def test_le_as_duas_listas():
    config = config_com(models=["a"], vision=["b"])
    assert modelos_configurados(config) == {"raciocínio": ["a"], "visão": ["b"]}


# --------------------------------------------------------------- o veredito


def test_tudo_vivo_sai_com_zero(capsys):
    resultado = {"raciocínio": [("a:free", True)], "visão": []}
    assert _veredito(resultado, []) == 0
    assert "existem no catálogo" in capsys.readouterr().out


def test_modelo_morto_sai_com_um_e_diz_qual(capsys):
    resultado = {"raciocínio": [("a:free", True), ("x:free", False)], "visão": []}
    assert _veredito(resultado, ["x:free"]) == 1
    saida = capsys.readouterr().out
    assert "x:free" in saida and "config.yaml" in saida


def test_papel_inteiro_morto_ganha_aviso_proprio(capsys):
    """Lista encurtada é aborrecimento; lista zerada é o papel fora do ar.

    Sem esta distinção, perder o último modelo de visão apareceria como só
    mais uma linha na lista de mortos.
    """
    resultado = {
        "raciocínio": [("a:free", True)],
        "visão": [("x:free", False), ("y:free", False)],
    }
    _veredito(resultado, ["x:free", "y:free"])
    saida = capsys.readouterr().out
    assert "NENHUM modelo de visão" in saida


def test_papel_vazio_nao_dispara_o_aviso(capsys):
    """Não configurar visão é uma escolha; não é o papel ter morrido."""
    _veredito({"raciocínio": [("a:free", True)], "visão": []}, [])
    assert "NENHUM modelo" not in capsys.readouterr().out
