"""Fetch HTTP que valida cada salto do redirecionamento.

`follow_redirects=True` do httpx é conveniente e, aqui, era uma brecha: o guard
aprovava a URL inicial e o cliente seguia sozinho para onde a resposta mandasse
— inclusive para 127.0.0.1, para o roteador da casa ou para o serviço de
metadados de nuvem. Aprovar o primeiro endereço não aprova o terceiro.

Então os redirecionamentos são seguidos à mão, e cada destino passa pela mesma
validação da URL original. É mais código que `follow_redirects=True`; é a
diferença entre validar uma vez e validar sempre.
"""

from __future__ import annotations

from urllib.parse import urljoin, urlparse

from james.logs import get_logger
from james.security.enderecos import EnderecoBloqueado, NaoResolveu, validar_host

logger = get_logger("james.web.http")

MAX_SALTOS = 5
_ESQUEMAS = ("http", "https")


class RedirecionamentoBloqueado(RuntimeError):
    """Um salto do redirecionamento apontava para onde não devia."""


class NomeNaoResolvido(RedirecionamentoBloqueado):
    """O nome não resolveu — barra igual, mas não é ataque, é rede."""


def _validar_url(url: str) -> str:
    partes = urlparse(url)
    if partes.scheme not in _ESQUEMAS:
        raise RedirecionamentoBloqueado(
            f"Esquema '{partes.scheme or 'ausente'}' não é permitido."
        )
    if not partes.hostname:
        raise RedirecionamentoBloqueado("Endereço sem host.")
    try:
        validar_host(partes.hostname)
    except NaoResolveu as exc:
        raise NomeNaoResolvido(str(exc)) from exc
    except EnderecoBloqueado as exc:
        raise RedirecionamentoBloqueado(str(exc)) from exc
    return url


def obter(client, url: str, *, metodo: str = "GET", **kwargs):
    """Faz a requisição seguindo redirecionamentos UM A UM, validando cada um.

    `client` é um `httpx.Client` construído com `follow_redirects=False` — a
    função não confia no cliente para isso, e por isso passa `follow_redirects`
    explicitamente em cada chamada.
    """
    atual = _validar_url(url)

    for salto in range(MAX_SALTOS + 1):
        resposta = client.request(metodo, atual, follow_redirects=False, **kwargs)
        if not resposta.is_redirect:
            return resposta

        destino = resposta.headers.get("location")
        if not destino:
            return resposta                      # 3xx sem Location: fim da linha

        # Relativo resolve contra a URL atual; absoluto substitui.
        atual = _validar_url(urljoin(atual, destino))
        logger.debug("Redirecionamento %d para %s", salto + 1, atual)

        # Depois de um redirecionamento, POST vira GET (o que os navegadores
        # fazem) e o corpo não é reenviado para outro destino.
        if metodo == "POST" and resposta.status_code in (301, 302, 303):
            metodo = "GET"
            kwargs.pop("data", None)
            kwargs.pop("json", None)
            kwargs.pop("content", None)

    raise RedirecionamentoBloqueado(
        f"Mais de {MAX_SALTOS} redirecionamentos — parei por segurança."
    )
