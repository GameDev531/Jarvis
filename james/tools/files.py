"""Organização de arquivos, restrita à whitelist de pastas.

Toda operação passa pelo `PathGuard`: origem E destino precisam estar dentro de
uma pasta permitida. Validar só a origem deixaria "mover para C:\\Windows"
passar, o que é o bug clássico deste tipo de ferramenta.

Duas regras que evitam perda de dados:
  - nada é sobrescrito: um destino já existente ganha sufixo numérico;
  - nada é apagado: não existe ferramenta de exclusão no catálogo, por design.

A categorização é por extensão, determinística. Chamar o modelo para decidir a
pasta de cada arquivo custaria uma requisição por arquivo e traria um palpite
onde uma tabela resolve — a organização por conteúdo é um passo posterior, com
outro desenho.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from james.logs import audit, get_logger
from james.permissions.paths import PathNotAllowed
from james.tools.registry import Tool, ToolRegistry, ToolResult

logger = get_logger("james.tools.files")

# Teto por operação: um "organize a pasta" que mexe em 5.000 arquivos deveria
# ser uma decisão consciente, não o efeito colateral de uma frase.
MAX_FILES_PER_RUN = 300
MAX_LISTED = 60

CATEGORIES: dict[str, tuple[str, ...]] = {
    "Imagens": (".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".svg", ".heic", ".tiff"),
    "Documentos": (".pdf", ".doc", ".docx", ".txt", ".rtf", ".odt", ".md", ".epub"),
    "Planilhas": (".xls", ".xlsx", ".csv", ".ods", ".tsv"),
    "Apresentacoes": (".ppt", ".pptx", ".odp"),
    "Videos": (".mp4", ".mkv", ".avi", ".mov", ".wmv", ".flv", ".webm"),
    "Audio": (".mp3", ".wav", ".flac", ".aac", ".ogg", ".m4a", ".wma"),
    "Compactados": (".zip", ".rar", ".7z", ".tar", ".gz", ".bz2", ".xz"),
    "Programas": (".exe", ".msi", ".bat", ".cmd", ".ps1", ".appx"),
    "Codigo": (".py", ".js", ".ts", ".html", ".css", ".json", ".java", ".c", ".cpp", ".cs", ".go", ".rs"),
}
_OTHER = "Outros"

_EXTENSION_MAP = {
    extension: category
    for category, extensions in CATEGORIES.items()
    for extension in extensions
}


def category_for(path: Path) -> str:
    return _EXTENSION_MAP.get(path.suffix.lower(), _OTHER)


def unique_destination(destination: Path) -> Path:
    """Caminho livre, acrescentando sufixo se necessário.

    Sobrescrever silenciosamente é a forma mais fácil de destruir dados numa
    ferramenta de organização.
    """
    if not destination.exists():
        return destination
    stem, suffix, parent = destination.stem, destination.suffix, destination.parent
    for index in range(1, 1000):
        candidate = parent / f"{stem} ({index}){suffix}"
        if not candidate.exists():
            return candidate
    raise OSError(f"Não consegui achar um nome livre para {destination.name}")


def register(registry: ToolRegistry, config, guard) -> None:
    def listar_arquivos(args: dict) -> ToolResult:
        pasta = str(args.get("pasta", "")).strip()
        try:
            raiz = guard.validate_path(pasta)
        except PathNotAllowed as exc:
            return ToolResult.failure(str(exc))
        if not raiz.is_dir():
            return ToolResult.failure("Isso não é uma pasta.")

        try:
            itens = sorted(
                item for item in raiz.iterdir() if not item.name.startswith(".")
            )
        except OSError as exc:
            logger.error("Não consegui listar %s: %s", raiz, exc)
            return ToolResult.failure("Não consegui ler essa pasta.")

        arquivos = [item.name for item in itens if item.is_file()][:MAX_LISTED]
        pastas = [item.name for item in itens if item.is_dir()][:MAX_LISTED]

        resumo = f"{len(arquivos)} arquivos e {len(pastas)} pastas em {raiz.name}."
        return ToolResult(
            ok=True,
            speech=resumo,
            data={"pasta": str(raiz), "arquivos": arquivos, "pastas": pastas},
        )

    def organizar_arquivos(args: dict) -> ToolResult:
        pasta = str(args.get("pasta", "")).strip()
        try:
            raiz = guard.validate_path(pasta)
        except PathNotAllowed as exc:
            return ToolResult.failure(str(exc))
        if not raiz.is_dir():
            return ToolResult.failure("Isso não é uma pasta.")

        try:
            arquivos = [
                item
                for item in raiz.iterdir()
                if item.is_file() and not item.name.startswith(".")
            ]
        except OSError as exc:
            logger.error("Não consegui listar %s: %s", raiz, exc)
            return ToolResult.failure("Não consegui ler essa pasta.")

        if not arquivos:
            return ToolResult(ok=True, speech="Essa pasta já está vazia de arquivos soltos.")
        if len(arquivos) > MAX_FILES_PER_RUN:
            return ToolResult.failure(
                f"São {len(arquivos)} arquivos, acima do meu limite de "
                f"{MAX_FILES_PER_RUN} por vez. Organize uma parte de cada vez."
            )

        movidos: dict[str, int] = {}
        falhas = 0
        for arquivo in arquivos:
            categoria = category_for(arquivo)
            destino_pasta = raiz / categoria
            try:
                destino_pasta.mkdir(exist_ok=True)
                destino = unique_destination(destino_pasta / arquivo.name)
                # Revalida o destino: a pasta de categoria é derivada da raiz,
                # mas um nome de arquivo esquisito não pode escapar dela.
                guard.validate_path(str(destino))
                shutil.move(str(arquivo), str(destino))
                movidos[categoria] = movidos.get(categoria, 0) + 1
            except (OSError, PathNotAllowed) as exc:
                logger.warning("Não consegui mover %s: %s", arquivo.name, exc)
                falhas += 1

        total = sum(movidos.values())
        audit("organizar_arquivos", pasta=str(raiz), movidos=total, falhas=falhas)

        if total == 0:
            return ToolResult.failure("Não consegui mover nenhum arquivo.")

        detalhe = ", ".join(f"{n} em {c}" for c, n in sorted(movidos.items()))
        fala = f"Organizei {total} arquivos: {detalhe}."
        if falhas:
            fala += f" {falhas} não puderam ser movidos."
        return ToolResult(
            ok=True, speech=fala, data={"movidos": movidos, "falhas": falhas}
        )

    def mover_arquivo(args: dict) -> ToolResult:
        origem_bruta = str(args.get("origem", "")).strip()
        destino_bruto = str(args.get("destino", "")).strip()
        try:
            # Os dois lados são validados: validar só a origem deixaria passar
            # um destino fora da whitelist.
            origem = guard.validate_path(origem_bruta)
            destino = guard.validate_path(destino_bruto)
        except PathNotAllowed as exc:
            return ToolResult.failure(str(exc))

        if not origem.exists():
            return ToolResult.failure("Não encontrei esse arquivo.")
        if destino.is_dir():
            destino = destino / origem.name

        try:
            destino = unique_destination(destino)
            destino.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(origem), str(destino))
        except OSError as exc:
            logger.error("Falha ao mover %s: %s", origem, exc)
            return ToolResult.failure("Não consegui mover o arquivo.")

        audit("mover_arquivo", origem=str(origem), destino=str(destino))
        return ToolResult(
            ok=True,
            ack=f"{origem.name} movido.",
            data={"origem": str(origem), "destino": str(destino)},
        )

    def renomear_arquivo(args: dict) -> ToolResult:
        caminho_bruto = str(args.get("caminho", "")).strip()
        novo_nome = str(args.get("novo_nome", "")).strip()
        if not novo_nome:
            return ToolResult.failure("Preciso do novo nome.")

        # Só o nome, nunca um caminho: aceitar "../x" aqui seria uma travessia
        # disfarçada de renomeação.
        if Path(novo_nome).name != novo_nome or novo_nome in (".", ".."):
            return ToolResult.failure("O novo nome não pode conter caminho.")

        try:
            origem = guard.validate_path(caminho_bruto)
            destino = guard.validate_path(str(origem.parent / novo_nome))
        except PathNotAllowed as exc:
            return ToolResult.failure(str(exc))

        if not origem.exists():
            return ToolResult.failure("Não encontrei esse arquivo.")

        try:
            destino = unique_destination(destino)
            origem.rename(destino)
        except OSError as exc:
            logger.error("Falha ao renomear %s: %s", origem, exc)
            return ToolResult.failure("Não consegui renomear o arquivo.")

        audit("renomear_arquivo", de=str(origem), para=str(destino))
        return ToolResult(
            ok=True, ack=f"Renomeado para {destino.name}.", data={"novo": str(destino)}
        )

    registry.register(
        Tool(
            name="listar_arquivos",
            description="Lista os arquivos e pastas de uma pasta permitida.",
            parameters={
                "type": "object",
                "properties": {
                    "pasta": {"type": "string", "description": "Caminho completo da pasta."}
                },
                "required": ["pasta"],
            },
            handler=listar_arquivos,
            fire_and_forget=True,
        )
    )
    registry.register(
        Tool(
            name="organizar_arquivos",
            description=(
                "Organiza os arquivos soltos de uma pasta em subpastas por tipo "
                "(Imagens, Documentos, Planilhas, Vídeos e assim por diante)."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "pasta": {"type": "string", "description": "Caminho completo da pasta."}
                },
                "required": ["pasta"],
            },
            handler=organizar_arquivos,
            fire_and_forget=True,
        )
    )
    registry.register(
        Tool(
            name="mover_arquivo",
            description="Move um arquivo de uma pasta permitida para outra.",
            parameters={
                "type": "object",
                "properties": {
                    "origem": {"type": "string", "description": "Caminho completo do arquivo."},
                    "destino": {"type": "string", "description": "Pasta ou caminho de destino."},
                },
                "required": ["origem", "destino"],
            },
            handler=mover_arquivo,
            fire_and_forget=True,
        )
    )
    registry.register(
        Tool(
            name="renomear_arquivo",
            description="Renomeia um arquivo dentro de uma pasta permitida.",
            parameters={
                "type": "object",
                "properties": {
                    "caminho": {"type": "string", "description": "Caminho completo do arquivo."},
                    "novo_nome": {
                        "type": "string",
                        "description": "Novo nome do arquivo, sem caminho.",
                    },
                },
                "required": ["caminho", "novo_nome"],
            },
            handler=renomear_arquivo,
            fire_and_forget=True,
        )
    )
