"""Servidor local da interface holográfica.

Serve `ui/web/` e empurra o estado do James por SSE. Roda só enquanto o modo
holograma está ligado.

## Por que o navegador, e não um QWebEngineView

A interface é Three.js com bloom aditivo — o mesmo tipo de carga que fez o
overlay que cobria a tela travar a máquina. Embutir um Chromium dentro do
processo do James colocaria essa carga para disputar CPU com o pipeline de voz,
que é a única coisa que não pode engasgar.

No navegador ela roda em outro processo, com a GPU que o navegador já sabe usar,
podendo ir para um segundo monitor. Se travar, você fecha a aba e o James nem
percebe. O custo dentro do James é um socket e uma thread.

## As quatro travas

1. **127.0.0.1, nunca 0.0.0.0.** Ligar em todas as interfaces entregaria o
   comando de voz do James para qualquer um na mesma rede.
2. **Token por sessão.** Sem ele, qualquer site aberto no seu navegador poderia
   fazer POST para `localhost` e mandar no James — o navegador envia a
   requisição de bom grado. O token nasce a cada ativação do modo, vai na URL
   que o navegador abre, e sem ele todo POST é 403.
3. **Origem verificada.** Requisições com `Origin` de outro site são recusadas,
   mesmo com o token certo.
4. **Raiz de arquivos fechada.** Todo caminho é resolvido e comparado com a
   pasta `ui/web`; o que cair fora vira 404, incluindo `..%2f` e link simbólico.

O texto que chega pelo campo de comando entra no orquestrador como um turno
normal — passa pelo mesmo LLM e pelo mesmo guard que uma ordem falada. A tela
não tem atalho para o sistema.
"""

from __future__ import annotations

import json
import mimetypes
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from james.logs import audit, get_logger
from james.ui.bus import StateBus

logger = get_logger("james.ui.web")

# Texto maior que isto não é comando de voz, é tentativa de entupir o processo.
MAX_COMANDO = 2000
_MAX_CORPO = 8192

# De quanto em quanto tempo o SSE manda um comentário de keep-alive. Sem isso,
# proxies e o próprio navegador derrubam a conexão ociosa.
_KEEPALIVE_S = 15.0

_TIPOS = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".png": "image/png",
    ".svg": "image/svg+xml",
    ".glb": "model/gltf-binary",
    ".gltf": "model/gltf+json",
}


class WebInterfaceServer:
    """HTTP + SSE em 127.0.0.1. `start()` devolve a URL com o token."""

    def __init__(
        self,
        raiz: Path,
        bus: StateBus,
        *,
        on_comando=None,
        on_escutar=None,
        porta: int = 0,
        modelos: Path | None = None,
    ) -> None:
        self.raiz = Path(raiz).resolve()
        # Cache de modelos 3D baixados. Fica FORA da raiz do código de
        # propósito: conteúdo gerado não se mistura com fonte versionada, e
        # assim um modelo baixado nunca pode sobrescrever um arquivo da
        # interface — nem que o nome bata.
        self.modelos = Path(modelos).resolve() if modelos else None
        self.bus = bus
        self.on_comando = on_comando
        self.on_escutar = on_escutar
        self.porta_pedida = int(porta)
        self.token = secrets.token_urlsafe(24)
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    # ------------------------------------------------------------ ciclo de vida

    def start(self) -> str:
        if self._server is not None:
            return self.url

        if not (self.raiz / "index.html").is_file():
            raise FileNotFoundError(
                f"Interface não encontrada em {self.raiz} (falta index.html)."
            )

        handler = _build_handler(self)
        # 127.0.0.1 fixo: ver a trava 1 no topo do arquivo.
        self._server = ThreadingHTTPServer(("127.0.0.1", self.porta_pedida), handler)
        self._server.daemon_threads = True
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            kwargs={"poll_interval": 0.2},
            name="james-web",
            daemon=True,
        )
        self._thread.start()
        # A porta, NUNCA a URL: `self.url` carrega o token de sessão, e o log
        # vai para arquivo — que acaba em ZIP, em print de tela, em anexo de
        # e-mail pedindo ajuda. Um token num log é um token público.
        logger.info("Interface holográfica em http://127.0.0.1:%d/", self.porta)
        audit("interface_web_ligada", porta=self.porta)
        return self.url

    def stop(self) -> None:
        servidor, self._server = self._server, None
        thread, self._thread = self._thread, None
        if servidor is not None:
            servidor.shutdown()
            servidor.server_close()
        if thread is not None and thread.is_alive():
            thread.join(timeout=3.0)
        self.bus.close()
        audit("interface_web_desligada")

    @property
    def porta(self) -> int:
        return self._server.server_address[1] if self._server else 0

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.porta}/?token={self.token}"

    # -------------------------------------------------------------- segurança

    def caminho_de(self, rota: str) -> Path | None:
        """Resolve uma rota. None quando escapa da raiz ou não existe."""
        limpo = rota.split("?", 1)[0].lstrip("/") or "index.html"

        # /models/* sai do cache, não do código. Só nomes de arquivo simples:
        # o slug já é sanitizado do lado do JS, mas quem serve não confia em
        # quem pede — uma barra aqui seria travessia.
        if limpo.startswith("models/"):
            if self.modelos is None:
                return None
            nome = limpo[len("models/"):]
            if not nome or "/" in nome or "\\" in nome or nome.startswith("."):
                return None
            base = self.modelos
        else:
            base = self.raiz
            nome = limpo

        try:
            alvo = (base / nome).resolve()
        except (OSError, ValueError):
            return None
        # `is_relative_to` compara depois de resolver, então pega `..` e link
        # simbólico apontando para fora — os dois jeitos clássicos de escapar.
        if not alvo.is_relative_to(base) or not alvo.is_file():
            return None
        return alvo

    def autorizado(self, token: str | None, origem: str | None) -> bool:
        if not token or not secrets.compare_digest(token, self.token):
            return False
        if origem and not origem.startswith(f"http://127.0.0.1:{self.porta}"):
            # Trava 3: um site externo não manda no James nem com o token.
            logger.warning("Origem recusada na interface web: %s", origem)
            return False
        return True

    # ---------------------------------------------------------------- comandos

    def executar_comando(self, texto: str) -> tuple[bool, str]:
        texto = str(texto or "").strip()
        if not texto:
            return False, "comando vazio"
        if len(texto) > MAX_COMANDO:
            return False, "comando longo demais"
        if self.on_comando is None:
            return False, "sem orquestrador ligado"
        audit("comando_pela_interface", tamanho=len(texto))
        self.on_comando(texto)
        return True, "aceito"


def _build_handler(app: WebInterfaceServer):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = "James"
        sys_version = ""

        # ------------------------------------------------------------ utilidades

        def log_message(self, fmt, *args):  # noqa: N802 — assinatura do módulo
            logger.debug("web: " + fmt, *args)

        def _token(self) -> str | None:
            query = parse_qs(urlsplit(self.path).query)
            do_header = self.headers.get("X-James-Token")
            return do_header or (query.get("token") or [None])[0]

        def _responder(self, status: int, corpo: bytes, tipo: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", tipo)
            self.send_header("Content-Length", str(len(corpo)))
            # A interface não deve ser embutida em lugar nenhum.
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Referrer-Policy", "no-referrer")
            self.end_headers()
            self.wfile.write(corpo)

        def _json(self, status: int, dados: dict) -> None:
            self._responder(status, json.dumps(dados).encode("utf-8"), "application/json")

        # ------------------------------------------------------------------ GET

        def do_GET(self) -> None:  # noqa: N802 — assinatura do módulo
            rota = urlsplit(self.path).path
            if rota == "/events":
                # O fluxo carrega transcrição do que você falou — é a coisa mais
                # sensível que o James tem. Sem token, ele não abre: senão
                # qualquer processo local leria a conversa inteira.
                if not app.autorizado(self._token(), self.headers.get("Origin")):
                    self._json(403, {"erro": "token invalido"})
                    return
                self._sse()
                return

            caminho = app.caminho_de(rota)
            if caminho is None:
                self._responder(404, b"nao encontrado", "text/plain; charset=utf-8")
                return

            tipo = _TIPOS.get(caminho.suffix) or (
                mimetypes.guess_type(caminho.name)[0] or "application/octet-stream"
            )
            try:
                corpo = caminho.read_bytes()
            except OSError as exc:
                logger.error("Falha ao ler %s: %s", caminho, exc)
                self._responder(500, b"erro de leitura", "text/plain; charset=utf-8")
                return
            self._responder(200, corpo, tipo)

        def _sse(self) -> None:
            """Fluxo de estado. Só termina quando o navegador fecha a conexão."""
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.end_headers()

            with app.bus.subscribe() as assinante:
                try:
                    while not assinante.fechado:
                        evento = assinante.receber(timeout=_KEEPALIVE_S)
                        if evento is None:
                            # Comentário SSE: mantém a conexão viva sem virar dado.
                            self.wfile.write(b": ping\n\n")
                        else:
                            dados = json.dumps(evento, ensure_ascii=False)
                            self.wfile.write(f"data: {dados}\n\n".encode("utf-8"))
                        self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError, OSError):
                    # Aba fechada. É o fim normal deste laço, não um erro.
                    pass

        # ----------------------------------------------------------------- POST

        def do_POST(self) -> None:  # noqa: N802 — assinatura do módulo
            rota = urlsplit(self.path).path
            if not app.autorizado(self._token(), self.headers.get("Origin")):
                self._json(403, {"erro": "token invalido"})
                return

            tamanho = int(self.headers.get("Content-Length") or 0)
            if tamanho > _MAX_CORPO:
                self._json(413, {"erro": "corpo grande demais"})
                return
            bruto = self.rfile.read(tamanho) if tamanho > 0 else b"{}"

            if rota == "/escutar":
                if app.on_escutar is not None:
                    app.on_escutar()
                self._json(200, {"ok": True})
                return

            if rota != "/comando":
                self._json(404, {"erro": "rota desconhecida"})
                return

            try:
                dados = json.loads(bruto.decode("utf-8") or "{}")
            except (UnicodeDecodeError, json.JSONDecodeError):
                self._json(400, {"erro": "json invalido"})
                return

            ok, motivo = app.executar_comando(dados.get("texto", ""))
            self._json(200 if ok else 400, {"ok": ok, "motivo": motivo})

    return Handler
