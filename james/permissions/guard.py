"""Guard determinístico — decide SE uma ação pode rodar.

Separação de responsabilidade que sustenta a segurança do James:
    o LLM decide O QUE fazer;  o guard decide SE pode.

O guard nunca lê justificativa, "nível de risco" ou qualquer campo que o modelo
tenha produzido: ele revalida cada chamada contra regras fixas do config.yaml.
Isso vale mesmo quando o pedido chega de um resultado de busca ou de uma página
web — que são exatamente os vetores de prompt injection.

Invariante importante: para ações que executam algo (abrir app, abrir página),
é o GUARD quem resolve o comando/URL final, e a tool executa apenas o que veio
resolvido no veredito. Uma tool não consegue executar algo que o guard não
resolveu.
"""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass, field
from enum import Enum
from pathlib import PurePath
from typing import Any, Callable
from urllib.parse import unquote, urlsplit

from james.config import Config, normalize_text
from james.permissions.paths import PathGuard, PathNotAllowed
from james.security.sanitizer import strip_dangerous_chars

MAX_URL_LENGTH = 2048
MAX_QUERY_LENGTH = 400
_ALLOWED_SCHEMES = ("http", "https")
_VOLUME_ACTIONS = ("aumentar", "diminuir", "mutar")


class Decision(Enum):
    """Nível 1 = ALLOW, Nível 2 = CONFIRM, fora do catálogo = BLOCK."""

    ALLOW = "allow"
    CONFIRM = "confirm"
    BLOCK = "block"


@dataclass(frozen=True)
class GuardVerdict:
    tool: str
    decision: Decision
    reason: str
    spoken: str
    args: dict[str, Any] = field(default_factory=dict)

    @property
    def allowed(self) -> bool:
        return self.decision is Decision.ALLOW

    @property
    def needs_confirmation(self) -> bool:
        return self.decision is Decision.CONFIRM


class Guard:
    def __init__(self, config: Config) -> None:
        self.config = config
        self.treatment = str(config.get("persona.tratamento", "senhor"))
        self.apps = config.app_whitelist()
        self.path_guard = PathGuard(config.get("permissions.folders", []) or [])
        self.risky_url_patterns = config.normalized_list("permissions.risky_url_patterns")
        self.blocked_domains = [
            host for host in config.normalized_list("permissions.blocked_domains") if host
        ]
        self.blocked_schemes = set(config.normalized_list("permissions.blocked_url_schemes"))
        self.require_https = bool(config.get("permissions.require_https", True))

        self.vision_requires_confirmation = bool(
            config.get("permissions.vision_requires_confirmation", True)
        )

        self._rules: dict[str, Callable[[dict[str, Any]], GuardVerdict]] = {
            # --- baixo risco, executa direto ---
            "abrir_app": self._rule_abrir_app,
            "abrir_pagina": self._rule_abrir_pagina,
            # Ler página é navegação: passa pela MESMA validação de URL que
            # abrir uma — esquema, host interno e domínio bloqueado.
            "ler_pagina": self._rule_ler_pagina,
            "pesquisar_web": self._rule_pesquisar_web,
            "ajustar_volume": self._rule_ajustar_volume,
            "que_horas_sao": self._rule_sem_risco,
            "info_sistema": self._rule_sem_risco,
            # --- memória: nota pessoal, não ação no sistema ---
            "lembrar": self._rule_sem_risco,
            "esquecer": self._rule_sem_risco,
            "atualizar_memoria": self._rule_sem_risco,
            "consultar_memoria": self._rule_sem_risco,
            "registrar_fato": self._rule_sem_risco,
            "consultar_fatos": self._rule_sem_risco,
            "revisar_fato": self._rule_sem_risco,
            "habilidades": self._rule_sem_risco,
            "briefing_do_dia": self._rule_sem_risco,
            "analisar_acao": self._rule_sem_risco,
            "comparar_acoes": self._rule_sem_risco,
            # Coordena outras ferramentas e não age sozinha: a autoridade
            # fica em cada passo, avaliado individualmente na execução.
            "executar_sequencia": self._rule_sem_risco,
            # Mesma lógica: o especialista não ganha permissão nenhuma, cada
            # ferramenta que ele chama é avaliada individualmente.
            "delegar": self._rule_sem_risco,
            "buscar_na_web": self._rule_sem_risco,
            "pesquisa_aprofundada": self._rule_sem_risco,
            # --- criação de documento: escreve arquivo NOVO na whitelist,
            #     sem sobrescrever nada, então não é irreversível ---
            "criar_apresentacao": self._rule_criar_documento,
            "criar_planilha": self._rule_criar_documento,
            # --- arquivos ---
            "listar_arquivos": self._rule_listar_arquivos,
            "organizar_arquivos": self._rule_organizar_arquivos,
            "mover_arquivo": self._rule_mover_arquivo,
            "renomear_arquivo": self._rule_renomear_arquivo,
            # --- sensíveis ---
            "fechar_app": self._rule_fechar_app,
            "ver_tela": self._rule_ver_tela,
            "ver_camera": self._rule_ver_camera,
            "instalar_habilidade": self._rule_instalar_habilidade,
            # --- modos ---
            "listar_modos": self._rule_sem_risco,
            # Projetar desenha numa tela local: não toca no sistema, não sai
            # da máquina, e fechar a janela desfaz. Nível 1.
            "projetar_holograma": self._rule_sem_risco,
            "fechar_hologramas": self._rule_sem_risco,
            # --- navegador ---
            # Ler é Nível 1: abrir uma aba e inspecionar não mudam o mundo.
            # A URL passa pela MESMA validação de abrir_pagina — esquema, host
            # interno, domínio bloqueado.
            "abrir_aba": self._rule_abrir_aba,
            "listar_abas": self._rule_sem_risco,
            "inspecionar_pagina": self._rule_sem_risco,
            # Agir é Nível 2: clicar pode comprar uma passagem, e preencher
            # pode mandar um formulário. Acima disto há o que é IMPOSSÍVEL —
            # campo de senha e upload são recusados em browser/actions.py, e
            # nenhuma confirmação destrava.
            "preencher_campo": self._rule_agir_no_navegador,
            "clicar_em": self._rule_agir_no_navegador,
            "ativar_modo": self._rule_ativar_modo,
            # Desligar é Nível 1 sempre, por princípio (ver a regra).
            "desativar_modo": self._rule_desativar_modo,
        }

        # Modos que ocupam hardware de privacidade. Ligar qualquer um deles
        # exige confirmação; o gerente é a fonte da verdade em tempo de
        # execução, esta lista é o padrão de quando ele ainda não existe.
        self.sensitive_modes = {
            normalize_text(str(nome))
            for nome in (config.get("permissions.modos_sensiveis", ["gestos"]) or [])
        }

    # ------------------------------------------------------------ entrada

    @property
    def known_tools(self) -> tuple[str, ...]:
        return tuple(self._rules)

    def evaluate(self, tool: str, args: dict[str, Any] | None = None) -> GuardVerdict:
        """Avalia uma chamada de tool. Tool desconhecida é bloqueada por padrão."""
        arguments = dict(args or {})
        rule = self._rules.get(tool)
        if rule is None:
            return GuardVerdict(
                tool=tool,
                decision=Decision.BLOCK,
                reason=f"tool '{tool}' não está no catálogo permitido",
                spoken=f"Não tenho essa capacidade, {self.treatment}.",
            )
        try:
            return rule(arguments)
        except Exception as exc:  # noqa: BLE001 — falha no guard = negar, nunca liberar
            return GuardVerdict(
                tool=tool,
                decision=Decision.BLOCK,
                reason=f"erro ao avaliar a regra de '{tool}': {exc!r}",
                spoken=f"Não consegui validar essa ação com segurança, {self.treatment}.",
            )

    def validate_path(self, candidate: str):
        """Exposto para as tools de arquivo (Fase 6). Levanta PathNotAllowed."""
        return self.path_guard.validate(candidate)

    # ------------------------------------------------------------- regras

    def _rule_sem_risco(self, args: dict[str, Any]) -> GuardVerdict:
        tool = "leitura"
        return GuardVerdict(
            tool=tool,
            decision=Decision.ALLOW,
            reason="somente leitura, sem efeito no sistema",
            spoken="",
            args=args,
        )

    def _rule_abrir_app(self, args: dict[str, Any]) -> GuardVerdict:
        raw_name = str(args.get("nome", "") or "")
        name = normalize_text(strip_dangerous_chars(raw_name))
        if not name:
            return self._block("abrir_app", "nome do app vazio", "Qual programa, {t}?")

        # Casamento exato contra a whitelist normalizada. Nada de substring nem
        # aproximação: "chrome-malicioso" não pode herdar a permissão de "chrome".
        command = self.apps.get(name)
        if command is None:
            return self._block(
                "abrir_app",
                f"'{name}' fora da whitelist de apps",
                f"'{raw_name.strip()}' não está na minha lista de programas permitidos, {{t}}.",
            )
        return GuardVerdict(
            tool="abrir_app",
            decision=Decision.ALLOW,
            reason=f"'{name}' consta na whitelist",
            spoken="",
            args={"nome": name, "comando": command},
        )

    def _rule_fechar_app(self, args: dict[str, Any]) -> GuardVerdict:
        raw_name = str(args.get("nome", "") or "")
        name = normalize_text(strip_dangerous_chars(raw_name))
        if not name:
            return self._block("fechar_app", "nome do app vazio", "Qual programa, {t}?")
        command = self.apps.get(name)
        if command is None:
            return self._block(
                "fechar_app",
                f"'{name}' fora da whitelist de apps",
                f"'{raw_name.strip()}' não está na minha lista de programas permitidos, {{t}}.",
            )
        # Nível 2: fechar pode descartar trabalho não salvo — é irreversível
        # do ponto de vista do usuário.
        return GuardVerdict(
            tool="fechar_app",
            decision=Decision.CONFIRM,
            reason="fechar app pode descartar trabalho não salvo",
            spoken=f"Vou fechar o {name}. Trabalho não salvo será perdido. Confirma, {self.treatment}?",
            args={"nome": name, "comando": command},
        )

    def _rule_abrir_pagina(self, args: dict[str, Any]) -> GuardVerdict:
        return self._analyze_url("abrir_pagina", str(args.get("url", "") or ""))

    def _rule_ler_pagina(self, args: dict[str, Any]) -> GuardVerdict:
        return self._analyze_url("ler_pagina", str(args.get("url", "") or ""))

    def _rule_abrir_aba(self, args: dict[str, Any]) -> GuardVerdict:
        """Abrir aba é navegar: mesma validação de URL de abrir_pagina.

        Ter uma regra própria em vez de reaproveitar a de `abrir_pagina` seria
        criar uma segunda porta para a mesma casa — e a segunda porta é sempre
        a que alguém esquece de trancar.
        """
        return self._analyze_url("abrir_aba", str(args.get("url", "") or ""))

    def _rule_agir_no_navegador(self, args: dict[str, Any]) -> GuardVerdict:
        """Preencher e clicar: Nível 2, sempre.

        Um clique pode confirmar uma compra, enviar um e-mail ou apagar uma
        conta, e o guard não tem como saber qual botão é qual — a página é de
        terceiro e o rótulo pode mentir. Quando não dá para distinguir o
        inofensivo do irreversível, o certo é perguntar.
        """
        seletor = str(args.get("seletor", "") or "").strip()
        return GuardVerdict(
            tool="navegador",
            decision=Decision.CONFIRM,
            reason="acao no navegador pode ser irreversivel",
            spoken=(
                f"Vou agir em {seletor} na página aberta. "
                f"Confirma, {self.treatment}?"
            ),
            args=args,
        )

    def _rule_pesquisar_web(self, args: dict[str, Any]) -> GuardVerdict:
        raw_query = strip_dangerous_chars(str(args.get("query", "") or "")).strip()
        if not raw_query:
            return self._block("pesquisar_web", "query vazia", "Pesquisar o quê, {t}?")
        if len(raw_query) > MAX_QUERY_LENGTH:
            raw_query = raw_query[:MAX_QUERY_LENGTH]
        # A URL de busca é montada pelo James a partir de um domínio fixo, então
        # o texto da query não consegue redirecionar para outro destino.
        return GuardVerdict(
            tool="pesquisar_web",
            decision=Decision.ALLOW,
            reason="busca em domínio fixo, sem alteração no sistema",
            spoken="",
            args={"query": raw_query},
        )

    def _rule_ajustar_volume(self, args: dict[str, Any]) -> GuardVerdict:
        action = normalize_text(str(args.get("acao", "") or ""))
        if action not in _VOLUME_ACTIONS:
            return self._block(
                "ajustar_volume",
                f"ação de volume inválida: {action!r}",
                "Não entendi o que fazer com o volume, {t}.",
            )
        return GuardVerdict(
            tool="ajustar_volume",
            decision=Decision.ALLOW,
            reason="ajuste de volume é reversível e local",
            spoken="",
            args={"acao": action},
        )

    # ---------------------------------------------------------- arquivos

    def _resolved_path(self, tool: str, field: str, args: dict[str, Any]):
        """Valida um caminho contra a whitelist. Devolve (caminho, veredito_de_erro)."""
        raw = strip_dangerous_chars(str(args.get(field, "") or "")).strip()
        if not raw:
            return None, self._block(tool, f"campo '{field}' vazio", "Qual pasta ou arquivo, {t}?")
        try:
            return self.path_guard.validate(raw), None
        except PathNotAllowed as exc:
            return None, self._block(
                tool,
                f"{field}: {exc}",
                "Esse caminho está fora das pastas que posso tocar, {t}.",
            )

    def _rule_listar_arquivos(self, args: dict[str, Any]) -> GuardVerdict:
        path, blocked = self._resolved_path("listar_arquivos", "pasta", args)
        if blocked is not None:
            return blocked
        # Somente leitura dentro da whitelist: não altera nada.
        return GuardVerdict(
            tool="listar_arquivos",
            decision=Decision.ALLOW,
            reason=f"leitura de '{path}' dentro da whitelist",
            spoken="",
            args={"pasta": str(path)},
        )

    def _rule_organizar_arquivos(self, args: dict[str, Any]) -> GuardVerdict:
        path, blocked = self._resolved_path("organizar_arquivos", "pasta", args)
        if blocked is not None:
            return blocked
        # Move muitos arquivos de uma vez: desfazer à mão seria trabalhoso.
        return GuardVerdict(
            tool="organizar_arquivos",
            decision=Decision.CONFIRM,
            reason=f"move vários arquivos em '{path}'",
            spoken=(
                f"Vou reorganizar os arquivos de {path.name} em subpastas por tipo. "
                f"Confirma, {self.treatment}?"
            ),
            args={"pasta": str(path)},
        )

    def _rule_mover_arquivo(self, args: dict[str, Any]) -> GuardVerdict:
        origem, blocked = self._resolved_path("mover_arquivo", "origem", args)
        if blocked is not None:
            return blocked
        # O destino também é validado: checar só a origem deixaria passar um
        # "mover para fora da whitelist", que é o bypass clássico aqui.
        destino, blocked = self._resolved_path("mover_arquivo", "destino", args)
        if blocked is not None:
            return blocked
        return GuardVerdict(
            tool="mover_arquivo",
            decision=Decision.CONFIRM,
            reason=f"move '{origem}' para '{destino}'",
            spoken=(
                f"Vou mover {origem.name} para {destino.name}. Confirma, {self.treatment}?"
            ),
            args={"origem": str(origem), "destino": str(destino)},
        )

    def _rule_renomear_arquivo(self, args: dict[str, Any]) -> GuardVerdict:
        caminho, blocked = self._resolved_path("renomear_arquivo", "caminho", args)
        if blocked is not None:
            return blocked

        novo_nome = strip_dangerous_chars(str(args.get("novo_nome", "") or "")).strip()
        if not novo_nome:
            return self._block("renomear_arquivo", "novo nome vazio", "Qual o novo nome, {t}?")
        # Um nome com separador de caminho é travessia disfarçada de renomeação.
        if PurePath(novo_nome).name != novo_nome or novo_nome in (".", ".."):
            return self._block(
                "renomear_arquivo",
                f"novo nome contém caminho: {novo_nome!r}",
                "O novo nome não pode conter barras nem pastas, {t}.",
            )

        return GuardVerdict(
            tool="renomear_arquivo",
            decision=Decision.CONFIRM,
            reason=f"renomeia '{caminho}' para '{novo_nome}'",
            spoken=(
                f"Vou renomear {caminho.name} para {novo_nome}. Confirma, {self.treatment}?"
            ),
            args={"caminho": str(caminho), "novo_nome": novo_nome},
        )

    def _rule_criar_documento(self, args: dict[str, Any]) -> GuardVerdict:
        """Criar um arquivo novo é reversível: nada existente é tocado.

        A tool grava apenas dentro da whitelist e nunca sobrescreve — o destino
        ganha sufixo se já existir. Por isso é Nível 1, ao contrário de mover e
        renomear, que mexem em arquivos que já existem.
        """
        titulo = strip_dangerous_chars(str(args.get("titulo", "") or "")).strip()
        if not titulo:
            return self._block("criar_documento", "título vazio", "Qual o título, {t}?")
        if not self.path_guard.enabled:
            return self._block(
                "criar_documento",
                "nenhuma pasta permitida configurada",
                "Não tenho uma pasta onde possa salvar arquivos, {t}.",
            )
        return GuardVerdict(
            tool="criar_documento",
            decision=Decision.ALLOW,
            reason="cria arquivo novo dentro da whitelist, sem sobrescrever",
            spoken="",
            args=dict(args),
        )

    def _rule_instalar_habilidade(self, args: dict[str, Any]) -> GuardVerdict:
        """Baixar habilidade é baixar instruções de terceiros para seguir.

        O risco é o de um pacote desconhecido: o conteúdo entra no contexto e
        influencia decisões. Por isso Nível 2 com a fonte dita em voz alta — a
        pessoa precisa ouvir DE ONDE está vindo antes de aprovar.
        """
        fonte = strip_dangerous_chars(str(args.get("fonte", "") or "")).strip()
        nome = strip_dangerous_chars(str(args.get("nome", "") or "")).strip()
        if not fonte or not nome:
            return self._block(
                "instalar_habilidade",
                "fonte ou nome ausente",
                "Preciso saber qual habilidade e de onde, {t}.",
            )
        return GuardVerdict(
            tool="instalar_habilidade",
            decision=Decision.CONFIRM,
            reason=f"instala '{nome}' a partir de '{fonte}'",
            spoken=(
                f"Encontrei a habilidade {nome} no repositório {fonte}. "
                f"É código de terceiros. Posso instalar, {self.treatment}?"
            ),
            args={"fonte": fonte, "nome": nome},
        )

    # ----------------------------------------------------------- visão

    def _rule_ver_tela(self, args: dict[str, Any]) -> GuardVerdict:
        pergunta = strip_dangerous_chars(str(args.get("pergunta", "") or "")).strip()
        if not self.vision_requires_confirmation:
            return GuardVerdict(
                tool="ver_tela",
                decision=Decision.ALLOW,
                reason="confirmação de visão desativada no config",
                spoken="",
                args={"pergunta": pergunta},
            )
        # A tela pode conter senha, banco, conversa privada — e a captura sai
        # da máquina para ser analisada. Perguntar antes é o padrão.
        return GuardVerdict(
            tool="ver_tela",
            decision=Decision.CONFIRM,
            reason="captura de tela é enviada para análise na nuvem",
            spoken=(
                f"Vou capturar sua tela e enviá-la para análise. Confirma, {self.treatment}?"
            ),
            args={"pergunta": pergunta},
        )

    def _rule_ver_camera(self, args: dict[str, Any]) -> GuardVerdict:
        pergunta = strip_dangerous_chars(str(args.get("pergunta", "") or "")).strip()
        if not self.vision_requires_confirmation:
            return GuardVerdict(
                tool="ver_camera",
                decision=Decision.ALLOW,
                reason="confirmação de visão desativada no config",
                spoken="",
                args={"pergunta": pergunta},
            )
        return GuardVerdict(
            tool="ver_camera",
            decision=Decision.CONFIRM,
            reason="foto da webcam é enviada para análise na nuvem",
            spoken=(
                f"Vou tirar uma foto pela webcam e enviá-la para análise. "
                f"Confirma, {self.treatment}?"
            ),
            args={"pergunta": pergunta},
        )

    # ------------------------------------------------------------- modos

    def _rule_ativar_modo(self, args: dict[str, Any]) -> GuardVerdict:
        nome = normalize_text(strip_dangerous_chars(str(args.get("modo", "") or "")))
        if not nome:
            return self._block("ativar_modo", "modo sem nome", "Qual modo eu ligo, {t}?")

        if nome not in self.sensitive_modes:
            return GuardVerdict(
                tool="ativar_modo",
                decision=Decision.ALLOW,
                reason=f"modo '{nome}' não ocupa hardware sensível",
                spoken="",
                args={"modo": nome},
            )

        # Ligar um modo de câmera é diferente de tirar uma foto: a foto acaba,
        # o modo fica. Por isso a frase diz "até você mandar desligar" — quem
        # confirma precisa saber que está autorizando algo contínuo.
        return GuardVerdict(
            tool="ativar_modo",
            decision=Decision.CONFIRM,
            reason=f"modo '{nome}' mantém a câmera aberta enquanto estiver ligado",
            spoken=(
                f"Vou ligar o modo {nome} e manter a câmera aberta até você mandar "
                f"desligar. Confirma, {self.treatment}?"
            ),
            args={"modo": nome},
        )

    def _rule_desativar_modo(self, args: dict[str, Any]) -> GuardVerdict:
        """Desligar nunca é bloqueado nem confirmado.

        Não é descuido: é o desenho. Se desligar a câmera exigisse confirmação,
        haveria um estado em que o usuário pede para fechar a câmera e o
        sistema responde com uma pergunta — e se a confirmação falhasse (sem
        whisper.cpp, microfone ocupado, ruído), a câmera ficaria aberta contra
        a vontade dele. Um freio que às vezes não funciona não é freio.
        """
        nome = normalize_text(strip_dangerous_chars(str(args.get("modo", "") or "")))
        return GuardVerdict(
            tool="desativar_modo",
            decision=Decision.ALLOW,
            reason="desligar um modo libera recurso e nunca é negado",
            spoken="",
            args={"modo": nome},
        )

    # -------------------------------------------------------------- URLs

    def _analyze_url(self, tool: str, raw_url: str) -> GuardVerdict:
        url = strip_dangerous_chars(raw_url).strip()
        if not url:
            return self._block(tool, "url vazia", "Qual endereço, {t}?")
        if len(url) > MAX_URL_LENGTH:
            return self._block(tool, f"url longa demais ({len(url)} chars)", "Esse endereço é inválido, {t}.")

        parts = urlsplit(url)
        scheme = parts.scheme.lower()

        if not scheme:
            # "github.com/foo" — assume https e reinterpreta. Um caminho do
            # Windows ("C:\\...") NÃO cai aqui: urlsplit enxerga o drive como
            # esquema 'c', que é barrado logo abaixo.
            url = "https://" + url.lstrip("/")
            parts = urlsplit(url)
            scheme = parts.scheme.lower()

        if scheme in self.blocked_schemes:
            return self._block(
                tool, f"esquema '{scheme}' está na lista de bloqueio", "Não abro esse tipo de endereço, {t}."
            )
        if scheme not in _ALLOWED_SCHEMES:
            return self._block(
                tool, f"esquema '{scheme}' não permitido", "Não abro esse tipo de endereço, {t}."
            )

        # .hostname já descarta o truque `https://site-confiavel.com@site-malicioso.com`,
        # devolvendo o host real (site-malicioso.com).
        host = (parts.hostname or "").lower().strip(".")
        if not host:
            return self._block(tool, "url sem host", "Esse endereço é inválido, {t}.")

        blocked_ip = self._blocked_ip_reason(host)
        if blocked_ip:
            return self._block(tool, blocked_ip, "Não abro endereços internos da máquina ou da rede, {t}.")

        if self._is_blocked_domain(host):
            return self._block(
                tool, f"host '{host}' está na lista de bloqueio", "Esse domínio está bloqueado, {t}."
            )

        # Percent-encoding é desfeito antes do casamento: `%63heckout` não pode
        # esconder "checkout".
        haystack = normalize_text(unquote(url))
        matched = [pattern for pattern in self.risky_url_patterns if pattern in haystack]

        if scheme == "http" and self.require_https:
            return GuardVerdict(
                tool=tool,
                decision=Decision.CONFIRM,
                reason=f"conexão sem HTTPS para '{host}'",
                spoken=(
                    f"O endereço {host} não usa conexão segura. Quer que eu abra mesmo assim, "
                    f"{self.treatment}?"
                ),
                args={"url": url, "host": host},
            )

        if matched:
            return GuardVerdict(
                tool=tool,
                decision=Decision.CONFIRM,
                reason=f"padrão sensível na url ({', '.join(sorted(set(matched)))})",
                spoken=(
                    f"Esse endereço parece envolver pagamento ou dados financeiros em {host}. "
                    f"Confirma que quer abrir, {self.treatment}?"
                ),
                args={"url": url, "host": host},
            )

        return GuardVerdict(
            tool=tool,
            decision=Decision.ALLOW,
            reason=f"navegação comum para '{host}'",
            spoken="",
            args={"url": url, "host": host},
        )

    def _blocked_ip_reason(self, host: str) -> str | None:
        """Barra IPs que não deveriam ser alcançados por comando de voz.

        Cobre loopback, rede privada, link-local (inclui o 169.254.169.254 de
        metadados de nuvem) e reservados — sem precisar listar cada um no
        config.
        """
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            return None
        if address.is_loopback:
            return f"'{host}' é loopback"
        if address.is_private:
            return f"'{host}' é endereço de rede privada"
        if address.is_link_local:
            return f"'{host}' é link-local (metadados de nuvem)"
        if address.is_reserved or address.is_unspecified or address.is_multicast:
            return f"'{host}' é endereço reservado"
        return None

    def _is_blocked_domain(self, host: str) -> bool:
        """Casa o host exato e seus subdomínios, nunca por substring.

        `evil-localhost.com` não pode ser barrado por conter "localhost", e
        `api.interno.local` é barrado se `interno.local` estiver na lista.
        """
        for blocked in self.blocked_domains:
            if host == blocked or host.endswith("." + blocked):
                return True
        return False

    # ---------------------------------------------------------- auxiliar

    def _block(self, tool: str, reason: str, spoken: str) -> GuardVerdict:
        return GuardVerdict(
            tool=tool,
            decision=Decision.BLOCK,
            reason=reason,
            spoken=spoken.replace("{t}", self.treatment),
        )
