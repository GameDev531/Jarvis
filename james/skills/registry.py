"""Habilidades — instruções especializadas carregadas sob demanda.

A ideia: antes de encarar uma tarefa de nicho, em vez de improvisar com
conhecimento genérico, o James lê um documento com as boas práticas daquele
assunto específico. É o mesmo mecanismo que um profissional usa ao consultar o
manual antes de mexer num equipamento que não é do dia a dia dele.

Por que isso importa mais aqui do que num assistente comum: o papel de
raciocínio roda em modelos gratuitos do OpenRouter, que são bons mas erram mais
que um modelo de ponta. Dar a eles uma referência concreta reduz muito o
"chute com confiança" — que é justamente o modo de falhar mais caro.

**Só carrega o que foi pedido.** Injetar todas as habilidades no prompt
derrotaria o propósito: o contexto encheria, o custo subiria e o modelo teria
mais texto irrelevante competindo por atenção. Cada `SKILL.md` traz um
cabeçalho com nome e descrição — é só isso que fica visível, e o conteúdo
inteiro só entra quando o modelo decide carregá-lo.

Formato de `skills/<nome>/SKILL.md`:

    ---
    name: nome-da-habilidade
    description: uma linha dizendo quando usar
    ---
    # Conteúdo em markdown
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import yaml

from james.config import normalize_text
from james.logs import get_logger
from james.security.sanitizer import strip_dangerous_chars

logger = get_logger("james.skills")

MAX_CONTEUDO = 12000
MAX_DESCRICAO = 300
_FRONTMATTER = re.compile(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", re.DOTALL)
# Nome de habilidade vira nome de pasta: nada de separador de caminho aqui.
_NOME_VALIDO = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,62}$")


@dataclass
class Skill:
    nome: str
    descricao: str
    caminho: Path
    fonte: str = "local"

    def to_dict(self) -> dict:
        return {"nome": self.nome, "descricao": self.descricao, "fonte": self.fonte}


def parse_skill_file(texto: str) -> tuple[dict, str]:
    """Separa o cabeçalho do corpo. Devolve (metadados, conteúdo)."""
    match = _FRONTMATTER.match(texto or "")
    if not match:
        return {}, (texto or "").strip()
    try:
        metadados = yaml.safe_load(match.group(1)) or {}
    except yaml.YAMLError as exc:
        logger.warning("Cabeçalho de habilidade ilegível: %s", exc)
        metadados = {}
    if not isinstance(metadados, dict):
        metadados = {}
    return metadados, match.group(2).strip()


class SkillRegistry:
    def __init__(self, directory: str | Path) -> None:
        self.directory = Path(directory)
        self._cache: dict[str, Skill] = {}
        self._assinatura: tuple | None = None

    # ------------------------------------------------------------- varredura

    def _assinatura_atual(self) -> tuple:
        """Impressão digital da pasta, para recarregar só quando algo mudou."""
        try:
            arquivos = sorted(self.directory.glob("*/SKILL.md"))
            return tuple((str(a), a.stat().st_mtime_ns) for a in arquivos)
        except OSError:
            return ()

    def refresh(self, forcar: bool = False) -> None:
        assinatura = self._assinatura_atual()
        if not forcar and assinatura == self._assinatura:
            return

        encontradas: dict[str, Skill] = {}
        for arquivo in sorted(self.directory.glob("*/SKILL.md")):
            skill = self._ler_cabecalho(arquivo)
            if skill is not None:
                encontradas[normalize_text(skill.nome)] = skill

        self._cache = encontradas
        self._assinatura = assinatura
        logger.info("%d habilidade(s) disponível(is).", len(encontradas))

    def _ler_cabecalho(self, arquivo: Path) -> Skill | None:
        try:
            # Só o começo: o cabeçalho está no topo e o corpo pode ser grande.
            with arquivo.open("r", encoding="utf-8") as handle:
                inicio = handle.read(4096)
        except OSError as exc:
            logger.warning("Não consegui ler %s: %s", arquivo, exc)
            return None

        metadados, _ = parse_skill_file(inicio)
        nome = str(metadados.get("name") or arquivo.parent.name).strip()
        if not _NOME_VALIDO.match(nome):
            logger.warning("Habilidade com nome inválido ignorada: %r", nome)
            return None

        descricao = strip_dangerous_chars(str(metadados.get("description") or "")).strip()
        if not descricao:
            descricao = "(sem descrição)"
        return Skill(
            nome=nome,
            descricao=descricao[:MAX_DESCRICAO],
            caminho=arquivo,
            fonte=str(metadados.get("source") or "local"),
        )

    # --------------------------------------------------------------- consulta

    def list_all(self) -> list[Skill]:
        self.refresh()
        return sorted(self._cache.values(), key=lambda s: s.nome)

    def get(self, nome: str) -> Skill | None:
        self.refresh()
        return self._cache.get(normalize_text(nome))

    def search(self, consulta: str, limite: int = 8) -> list[Skill]:
        """Busca no nome e na descrição, com o nome pesando mais."""
        self.refresh()
        termos = [t for t in normalize_text(consulta).split() if len(t) >= 2]
        if not termos:
            return []

        pontuadas: list[tuple[int, Skill]] = []
        for skill in self._cache.values():
            nome = normalize_text(skill.nome)
            descricao = normalize_text(skill.descricao)
            pontos = sum(3 for t in termos if t in nome)
            pontos += sum(1 for t in termos if t in descricao)
            if pontos:
                pontuadas.append((pontos, skill))

        pontuadas.sort(key=lambda item: (-item[0], item[1].nome))
        return [skill for _, skill in pontuadas[:limite]]

    def load(self, nome: str) -> str | None:
        """Conteúdo completo da habilidade, pronto para entrar no contexto."""
        skill = self.get(nome)
        if skill is None:
            return None
        try:
            bruto = skill.caminho.read_text(encoding="utf-8")
        except OSError as exc:
            logger.error("Não consegui carregar %s: %s", skill.caminho, exc)
            return None

        _, conteudo = parse_skill_file(bruto)
        # Habilidade instalada de fonte remota é conteúdo de terceiros entrando
        # no contexto: os invisíveis usados para esconder instrução saem aqui.
        conteudo = strip_dangerous_chars(conteudo)
        if len(conteudo) > MAX_CONTEUDO:
            conteudo = conteudo[:MAX_CONTEUDO].rstrip() + "\n\n[...habilidade truncada]"
        return conteudo
