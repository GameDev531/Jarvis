"""Agentes especialistas com recorte de catálogo.

Resolve dois problemas de uma vez.

**O problema de atenção.** O catálogo passou de vinte e cinco ferramentas, e a
própria pesquisa que originou este projeto já avisava: descrições demais
competindo por atenção fazem modelos rápidos errarem mais na escolha. Um
especialista que enxerga cinco ferramentas escolhe melhor que um generalista
que enxerga trinta.

**O problema de contexto.** Uma investigação longa enche o histórico de material
bruto — páginas inteiras, listas de resultados. Rodando num agente separado,
esse material morre com ele: o agente principal recebe só a conclusão.

Como funciona: o agente principal delega uma tarefa a um perfil. O perfil roda
com conversa própria e **apenas as ferramentas do seu recorte**, executa o que
precisar, e devolve um resumo.

O que NÃO muda: cada ferramenta que um especialista chama passa pelo mesmo
guard, com as mesmas regras. Delegar não é caminho para contornar permissão —
é só quem pede que muda, nunca o que é permitido.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from james.llm.base import NoProviderAvailable, ToolSchema
from james.llm.history import Conversation
from james.logs import audit, audit_text, get_logger
from james.permissions.guard import Decision

logger = get_logger("james.agent.team")

MAX_ITERACOES = 4
# Um especialista não pode delegar nem orquestrar: senão um plano poderia
# chamar um agente que chama outro agente, sem fim.
FERRAMENTAS_PROIBIDAS = frozenset({"delegar", "executar_sequencia"})

_INSTRUCAO = """\
Você é um agente especialista trabalhando para o James. Seu papel: {descricao}

Execute a tarefa abaixo usando as ferramentas que você tem. Quando terminar,
responda com um resumo objetivo do que descobriu ou fez, em no máximo quatro
frases — quem vai ler é o James, para repassar ao usuário.

Não faça perguntas: você não tem como recebê-las de volta. Se faltar
informação, faça a melhor tentativa possível e diga o que ficou faltando.

TAREFA: {tarefa}"""


@dataclass
class Perfil:
    nome: str
    descricao: str
    ferramentas: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "nome": self.nome,
            "descricao": self.descricao,
            "ferramentas": list(self.ferramentas),
        }


@dataclass
class ResultadoAgente:
    perfil: str
    tarefa: str
    resumo: str = ""
    ferramentas_usadas: list[str] = field(default_factory=list)
    concluido: bool = False
    erro: str = ""


def carregar_perfis(config, catalogo: tuple[str, ...]) -> dict[str, Perfil]:
    """Lê os perfis do config, descartando ferramentas que não existem.

    Um perfil que cita ferramenta inexistente é erro de configuração, não de
    runtime — avisar aqui é melhor que o especialista descobrir no meio da
    tarefa que não tem o que precisa.
    """
    perfis: dict[str, Perfil] = {}
    disponiveis = set(catalogo)

    for nome, dados in (config.section("agentes.perfis") or {}).items():
        if not isinstance(dados, dict):
            continue
        pedidas = [str(f).strip() for f in (dados.get("ferramentas") or [])]
        validas = tuple(f for f in pedidas if f in disponiveis and f not in FERRAMENTAS_PROIBIDAS)

        ausentes = [f for f in pedidas if f not in disponiveis]
        if ausentes:
            logger.warning(
                "Perfil '%s' cita ferramenta inexistente: %s", nome, ", ".join(ausentes)
            )
        if not validas:
            logger.warning("Perfil '%s' ficou sem ferramenta válida; ignorado.", nome)
            continue

        chave = str(nome).strip().lower()
        perfis[chave] = Perfil(
            nome=chave,
            descricao=" ".join(str(dados.get("descricao") or "").split()),
            ferramentas=validas,
        )
    return perfis


class SubAgente:
    """Roda uma tarefa com conversa própria e catálogo reduzido."""

    def __init__(
        self,
        perfil: Perfil,
        llm,
        guard,
        registry,
        confirm: Callable[[str], bool] | None = None,
        on_progress: Callable[[str, str], None] | None = None,
    ) -> None:
        self.perfil = perfil
        self.llm = llm
        self.guard = guard
        self.registry = registry
        self.confirm = confirm
        self.on_progress = on_progress

    def _schemas(self) -> list[ToolSchema]:
        permitidas = set(self.perfil.ferramentas)
        return [s for s in self.registry.schemas() if s.name in permitidas]

    def run(self, tarefa: str) -> ResultadoAgente:
        resultado = ResultadoAgente(perfil=self.perfil.nome, tarefa=tarefa)
        # Conversa própria: o material bruto que o especialista acumular morre
        # com ele, e não polui o histórico do agente principal.
        conversa = Conversation(max_turns=10)
        schemas = self._schemas()
        if not schemas:
            resultado.erro = "perfil sem ferramentas disponíveis"
            return resultado

        audit("agente_iniciado", perfil=self.perfil.nome, **audit_text(tarefa, campo="tarefa"))
        self._anunciar("começando")

        texto = _INSTRUCAO.format(descricao=self.perfil.descricao, tarefa=tarefa)
        ferramentas_originais = self.llm.tools

        try:
            for iteracao in range(MAX_ITERACOES):
                try:
                    # O recorte de catálogo é o ponto todo deste módulo: o
                    # especialista só enxerga as ferramentas do seu perfil.
                    self.llm.tools = schemas
                    resposta = self.llm.reason(conversa, text=texto)
                finally:
                    self.llm.tools = ferramentas_originais

                # Mesmo contrato do agente principal: o turno atual viaja fora
                # do histórico e só é consolidado depois da requisição. Antes
                # ele nunca era consolidado — e a partir da segunda iteração o
                # especialista perdia o enunciado da própria tarefa.
                conversa.add_user_text(texto)
                conversa.add_model_response(resposta.text, resposta.tool_calls)

                if not resposta.tool_calls:
                    resultado.resumo = resposta.text.strip()
                    resultado.concluido = bool(resultado.resumo)
                    break

                for chamada in resposta.tool_calls:
                    self._executar(chamada, conversa, resultado)

                texto = (
                    "Com base no que você acabou de obter, conclua a tarefa e "
                    "responda com o resumo final."
                )
            else:
                resultado.erro = f"não concluiu em {MAX_ITERACOES} iterações"

        except NoProviderAvailable as exc:
            resultado.erro = f"sem modelo disponível: {exc}"

        audit(
            "agente_concluido",
            perfil=self.perfil.nome,
            concluido=resultado.concluido,
            ferramentas=resultado.ferramentas_usadas,
            erro=resultado.erro,
        )
        return resultado

    def _executar(self, chamada, conversa: Conversation, resultado: ResultadoAgente) -> None:
        # Um especialista não pode chamar o que está fora do seu recorte, mesmo
        # que o modelo invente o nome.
        if chamada.name not in self.perfil.ferramentas:
            conversa.add_tool_result(
                chamada.name,
                {"status": "indisponivel", "motivo": "fora do seu conjunto de ferramentas"},
                chamada.call_id,
            )
            return

        self._anunciar(chamada.name)
        verdict = self.guard.evaluate(chamada.name, chamada.args)

        if verdict.decision is Decision.BLOCK:
            conversa.add_tool_result(
                chamada.name,
                {"status": "bloqueado", "motivo": verdict.reason},
                chamada.call_id,
            )
            return

        if verdict.decision is Decision.CONFIRM:
            if self.confirm is None or not self.confirm(verdict.spoken):
                conversa.add_tool_result(
                    chamada.name, {"status": "cancelado"}, chamada.call_id
                )
                return

        saida = self.registry.execute(chamada.name, verdict.args)
        resultado.ferramentas_usadas.append(chamada.name)
        conversa.add_tool_result(
            chamada.name,
            saida.data if saida.data is not None else {"status": "ok" if saida.ok else "erro"},
            chamada.call_id,
        )

    def _anunciar(self, etapa: str) -> None:
        if self.on_progress is None:
            return
        try:
            self.on_progress(self.perfil.nome, etapa)
        except Exception:  # noqa: BLE001 — progresso não pode derrubar o agente
            logger.exception("Erro ao anunciar progresso do agente.")
