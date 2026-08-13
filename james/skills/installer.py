"""Instalação de habilidades vindas de repositórios remotos.

Isto é, na prática, **baixar instruções de terceiros para o James seguir**. O
risco é o mesmo de um pacote npm ou pip desconhecido: o conteúdo entra no
contexto do modelo e influencia decisões. Por isso três travas, e não uma:

1. **Nível 2 no guard** — pede confirmação, sempre, mostrando a fonte.
2. **Lista de fontes confiáveis** no `config.yaml`. Fonte fora da lista é
   recusada aqui, antes de qualquer download.
3. **Saneamento na leitura** (`registry.load`) — os caracteres invisíveis
   usados para esconder instrução são removidos antes de o conteúdo chegar ao
   modelo.

Reaproveita o CLI `npx skills`, que já resolve busca e download. Isso exige
Node instalado; quando não estiver, a falha é explícita e diz o que fazer.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path

from james.logs import audit, get_logger

logger = get_logger("james.skills.installer")

_TIMEOUT_S = 180
# owner/repo — nada de URL arbitrária nem caminho local.
_FONTE_VALIDA = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,38}/[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")
_NOME_VALIDO = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,62}$")


class SkillInstallError(RuntimeError):
    """Instalação recusada ou falhou."""


def npx_disponivel() -> bool:
    return shutil.which("npx") is not None


def validar(fonte: str, nome: str, fontes_confiaveis: list[str]) -> tuple[str, str]:
    """Confere fonte e nome antes de qualquer acesso à rede."""
    fonte_limpa = str(fonte or "").strip()
    nome_limpo = str(nome or "").strip()

    if not _FONTE_VALIDA.match(fonte_limpa):
        raise SkillInstallError(
            f"'{fonte}' não é uma fonte válida. Use o formato dono/repositório."
        )
    if not _NOME_VALIDO.match(nome_limpo):
        raise SkillInstallError(f"'{nome}' não é um nome de habilidade válido.")

    permitidas = {str(f).strip().lower() for f in (fontes_confiaveis or [])}
    if not permitidas:
        raise SkillInstallError(
            "Nenhuma fonte confiável configurada em skills.fontes_confiaveis."
        )
    if fonte_limpa.lower() not in permitidas:
        raise SkillInstallError(
            f"A fonte '{fonte_limpa}' não está na lista de fontes confiáveis."
        )
    return fonte_limpa, nome_limpo


def install_skill(
    fonte: str, nome: str, destino: Path, fontes_confiaveis: list[str]
) -> Path:
    """Instala uma habilidade. Devolve o caminho do SKILL.md criado."""
    fonte_limpa, nome_limpo = validar(fonte, nome, fontes_confiaveis)

    if not npx_disponivel():
        raise SkillInstallError(
            "Instalar habilidades remotas exige Node.js instalado (comando npx)."
        )

    destino = Path(destino)
    destino.mkdir(parents=True, exist_ok=True)
    ja_existe = destino / nome_limpo / "SKILL.md"
    if ja_existe.exists():
        raise SkillInstallError(f"A habilidade '{nome_limpo}' já está instalada.")

    comando = [
        "npx", "--yes", "skills", "add", fonte_limpa,
        "--skill", nome_limpo,
        "--copy",
    ]
    logger.info("Instalando habilidade '%s' de %s.", nome_limpo, fonte_limpa)

    try:
        resultado = subprocess.run(
            comando,
            cwd=str(destino.parent),
            capture_output=True,
            timeout=_TIMEOUT_S,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)
            if sys.platform == "win32"
            else 0,
        )
    except subprocess.TimeoutExpired as exc:
        raise SkillInstallError(
            f"A instalação passou de {_TIMEOUT_S}s e foi cancelada."
        ) from exc
    except OSError as exc:
        raise SkillInstallError(f"Não consegui executar o npx: {exc}") from exc

    if resultado.returncode != 0:
        detalhe = resultado.stderr.decode("utf-8", errors="replace").strip()[-300:]
        raise SkillInstallError(f"A instalação falhou: {detalhe or 'erro desconhecido'}")

    # Confiar no código de saída não basta: o que importa é o arquivo existir
    # onde esperamos, com o nome que pedimos.
    instalado = destino / nome_limpo / "SKILL.md"
    if not instalado.exists():
        raise SkillInstallError(
            f"O comando terminou sem erro, mas '{nome_limpo}' não apareceu em {destino}."
        )

    audit("habilidade_instalada", nome=nome_limpo, fonte=fonte_limpa)
    return instalado
