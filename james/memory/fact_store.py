"""Memória profunda — fatos, entidades, busca textual e grau de confiança.

Complementa a camada curada (`curated_store.py`) em vez de substituí-la:

    MEMORY.md / USER.md   pouca coisa, sempre no contexto, você edita à mão
    fatos.db              muita coisa, consultada sob demanda, com procedência

A camada curada tem limite de caracteres justamente para caber no prompt toda
sessão. Esta não tem esse limite porque nada dela entra no prompt sozinho — o
James consulta quando precisa.

**Sem vetores, de propósito.** O projeto de referência (Hermes) usa vetores HRR
para uma operação de raciocínio composicional. É elegante e é bastante
matemática para manter. Fatos, entidades, busca textual e grau de confiança
cobrem a maior parte do valor prático, e SQLite com FTS5 já vem no Python — sem
dependência nenhuma.

**Grau de confiança.** Todo fato nasce em 0,5. Confirmar aproxima de 1; refutar
corta pela metade. Fatos abaixo do piso somem da busca mas não são apagados:
podem ser revistos, e apagar sozinho o que "parece errado" é como memória se
perde de verdade.

**Contradições.** Encontrar candidatos é trabalho de SQL: fatos que dividem as
mesmas entidades e têm sobreposição de termos. Julgar se de fato se contradizem
é trabalho do modelo. Mesma divisão do resto do projeto — o código entrega
candidatos, o modelo faz a leitura.
"""

from __future__ import annotations

import re
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from james.config import normalize_text
from james.logs import audit, get_logger

logger = get_logger("james.memory.facts")

CONFIANCA_INICIAL = 0.5
# Abaixo disto o fato some da busca. Não é apagado: refutar por engano
# não deveria destruir a informação.
PISO_DE_CONFIANCA = 0.15
MAX_FATOS = 5000
MAX_TEXTO = 500

_SCHEMA = """
CREATE TABLE IF NOT EXISTS fatos (
    id           INTEGER PRIMARY KEY,
    texto        TEXT    NOT NULL,
    -- Texto normalizado (minúsculo, sem acento) só para detectar duplicata.
    -- O lower() do SQLite é ASCII: 'LÉO' não vira 'léo', então comparar pelo
    -- texto original deixaria passar duplicata em qualquer palavra acentuada.
    chave        TEXT    NOT NULL DEFAULT '',
    fonte        TEXT    NOT NULL DEFAULT '',
    confianca    REAL    NOT NULL DEFAULT 0.5,
    confirmacoes INTEGER NOT NULL DEFAULT 0,
    refutacoes   INTEGER NOT NULL DEFAULT 0,
    criado_em    TEXT    NOT NULL,
    atualizado_em TEXT   NOT NULL
);

CREATE TABLE IF NOT EXISTS entidades (
    id     INTEGER PRIMARY KEY,
    chave  TEXT NOT NULL UNIQUE,   -- normalizada, para casar sem acento
    rotulo TEXT NOT NULL           -- como foi escrita, para exibir
);

CREATE TABLE IF NOT EXISTS fato_entidade (
    fato_id     INTEGER NOT NULL REFERENCES fatos(id) ON DELETE CASCADE,
    entidade_id INTEGER NOT NULL REFERENCES entidades(id) ON DELETE CASCADE,
    PRIMARY KEY (fato_id, entidade_id)
);

CREATE INDEX IF NOT EXISTS idx_fe_entidade ON fato_entidade(entidade_id);
CREATE INDEX IF NOT EXISTS idx_fatos_chave ON fatos(chave);
"""

# `content=` liga a tabela virtual à real: o texto não é duplicado, os gatilhos
# mantêm o índice em dia. `remove_diacritics 2` faz "cafe" encontrar "café",
# que em português é obrigatório.
_SCHEMA_FTS = """
CREATE VIRTUAL TABLE IF NOT EXISTS fatos_fts USING fts5(
    texto,
    content='fatos',
    content_rowid='id',
    tokenize="unicode61 remove_diacritics 2"
);

CREATE TRIGGER IF NOT EXISTS fatos_ai AFTER INSERT ON fatos BEGIN
    INSERT INTO fatos_fts(rowid, texto) VALUES (new.id, new.texto);
END;
CREATE TRIGGER IF NOT EXISTS fatos_ad AFTER DELETE ON fatos BEGIN
    INSERT INTO fatos_fts(fatos_fts, rowid, texto) VALUES('delete', old.id, old.texto);
END;
CREATE TRIGGER IF NOT EXISTS fatos_au AFTER UPDATE OF texto ON fatos BEGIN
    INSERT INTO fatos_fts(fatos_fts, rowid, texto) VALUES('delete', old.id, old.texto);
    INSERT INTO fatos_fts(rowid, texto) VALUES (new.id, new.texto);
END;
"""


class FactStoreFull(RuntimeError):
    """Limite de fatos atingido."""


@dataclass
class Fato:
    id: int
    texto: str
    confianca: float
    entidades: list[str]
    fonte: str = ""
    criado_em: str = ""
    confirmacoes: int = 0
    refutacoes: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "texto": self.texto,
            "confianca": round(self.confianca, 2),
            "entidades": self.entidades,
            "fonte": self.fonte,
            "criado_em": self.criado_em,
        }


class FactStore:
    def __init__(self, path: str | Path, max_fatos: int = MAX_FATOS) -> None:
        self.path = Path(path)
        self.max_fatos = max(10, int(max_fatos))
        self._lock = threading.RLock()
        self._fts = True
        self._connection: sqlite3.Connection | None = None
        self._setup()

    # ------------------------------------------------------------- conexão

    def _connect(self) -> sqlite3.Connection:
        if self._connection is None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            # check_same_thread=False porque o pipeline de voz e a interface
            # vivem em threads diferentes; o RLock serializa os acessos.
            self._connection = sqlite3.connect(
                str(self.path), check_same_thread=False, timeout=10.0
            )
            self._connection.row_factory = sqlite3.Row
            self._connection.execute("PRAGMA foreign_keys = ON")
            self._connection.execute("PRAGMA journal_mode = WAL")
        return self._connection

    def _setup(self) -> None:
        try:
            con = self._connect()
            con.executescript(_SCHEMA)
            self._migrar(con)
            try:
                con.executescript(_SCHEMA_FTS)
            except sqlite3.OperationalError as exc:
                # Nem todo build de SQLite traz FTS5. Sem ele a busca cai para
                # LIKE: pior no ranking, mas continua funcionando.
                logger.warning("FTS5 indisponível (%s); busca usará LIKE.", exc)
                self._fts = False
            con.commit()
        except sqlite3.Error as exc:
            logger.error("Não consegui preparar o banco de fatos: %s", exc)
            raise

    @staticmethod
    def _migrar(con: sqlite3.Connection) -> None:
        """Acrescenta colunas que versões anteriores do banco não tinham."""
        colunas = {linha["name"] for linha in con.execute("PRAGMA table_info(fatos)")}
        if "chave" not in colunas:
            con.execute("ALTER TABLE fatos ADD COLUMN chave TEXT NOT NULL DEFAULT ''")
            for linha in con.execute("SELECT id, texto FROM fatos").fetchall():
                con.execute(
                    "UPDATE fatos SET chave = ? WHERE id = ?",
                    (normalize_text(linha["texto"]), linha["id"]),
                )
            logger.info("Banco de fatos migrado: coluna de chave normalizada criada.")

    def close(self) -> None:
        with self._lock:
            if self._connection is not None:
                self._connection.close()
                self._connection = None

    # -------------------------------------------------------------- escrita

    def add(
        self, texto: str, entidades: list[str] | None = None, fonte: str = ""
    ) -> Fato | None:
        """Guarda um fato. None quando é duplicata exata."""
        conteudo = _limpar(texto)
        if not conteudo:
            return None
        if len(conteudo) > MAX_TEXTO:
            conteudo = conteudo[:MAX_TEXTO].rstrip() + "..."

        with self._lock:
            con = self._connect()
            chave = normalize_text(conteudo)
            existente = con.execute(
                "SELECT id FROM fatos WHERE chave = ?", (chave,)
            ).fetchone()
            if existente:
                # Repetir um fato é evidência de que ele é verdade.
                self.confirm(int(existente["id"]))
                return None

            total = con.execute("SELECT count(*) AS n FROM fatos").fetchone()["n"]
            if total >= self.max_fatos:
                raise FactStoreFull(
                    f"A memória profunda está no limite de {self.max_fatos} fatos. "
                    "Reveja o que já está guardado antes de adicionar mais."
                )

            agora = datetime.now(timezone.utc).isoformat(timespec="seconds")
            cursor = con.execute(
                "INSERT INTO fatos (texto, chave, fonte, confianca, criado_em, atualizado_em) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (conteudo, chave, str(fonte or "")[:120], CONFIANCA_INICIAL, agora, agora),
            )
            fato_id = int(cursor.lastrowid)
            rotulos = self._vincular(con, fato_id, entidades or [])
            con.commit()

        audit("fato_add", id=fato_id, texto=conteudo, entidades=rotulos)
        return Fato(
            id=fato_id,
            texto=conteudo,
            confianca=CONFIANCA_INICIAL,
            entidades=rotulos,
            fonte=fonte,
            criado_em=agora,
        )

    def _vincular(
        self, con: sqlite3.Connection, fato_id: int, entidades: list[str]
    ) -> list[str]:
        rotulos: list[str] = []
        for bruto in entidades[:12]:
            rotulo = _limpar(bruto)[:80]
            chave = normalize_text(rotulo)
            if not chave:
                continue
            con.execute(
                "INSERT OR IGNORE INTO entidades (chave, rotulo) VALUES (?, ?)",
                (chave, rotulo),
            )
            linha = con.execute(
                "SELECT id FROM entidades WHERE chave = ?", (chave,)
            ).fetchone()
            con.execute(
                "INSERT OR IGNORE INTO fato_entidade (fato_id, entidade_id) VALUES (?, ?)",
                (fato_id, int(linha["id"])),
            )
            rotulos.append(rotulo)
        return rotulos

    def remove(self, fato_id: int) -> bool:
        with self._lock:
            con = self._connect()
            cursor = con.execute("DELETE FROM fatos WHERE id = ?", (int(fato_id),))
            con.commit()
        removido = cursor.rowcount > 0
        if removido:
            audit("fato_remove", id=int(fato_id))
        return removido

    # ------------------------------------------------------------ confiança

    def confirm(self, fato_id: int) -> float | None:
        """Aproxima de 1 sem nunca chegar lá: certeza absoluta não existe."""
        return self._ajustar(fato_id, confirmando=True)

    def refute(self, fato_id: int) -> float | None:
        """Corta pela metade. Duas refutações já tiram o fato da busca."""
        return self._ajustar(fato_id, confirmando=False)

    def _ajustar(self, fato_id: int, confirmando: bool) -> float | None:
        with self._lock:
            con = self._connect()
            linha = con.execute(
                "SELECT confianca FROM fatos WHERE id = ?", (int(fato_id),)
            ).fetchone()
            if linha is None:
                return None

            atual = float(linha["confianca"])
            nova = atual + (1.0 - atual) * 0.3 if confirmando else atual * 0.5
            campo = "confirmacoes" if confirmando else "refutacoes"
            con.execute(
                f"UPDATE fatos SET confianca = ?, {campo} = {campo} + 1, "
                "atualizado_em = ? WHERE id = ?",
                (
                    nova,
                    datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    int(fato_id),
                ),
            )
            con.commit()

        audit("fato_confianca", id=int(fato_id), de=round(atual, 3), para=round(nova, 3))
        return nova

    # -------------------------------------------------------------- leitura

    def search(self, consulta: str, limite: int = 8, incluir_duvidosos: bool = False) -> list[Fato]:
        """Busca textual, com os mais confiáveis primeiro."""
        termos = _termos(consulta)
        if not termos:
            return []
        piso = -1.0 if incluir_duvidosos else PISO_DE_CONFIANCA

        with self._lock:
            con = self._connect()
            if self._fts:
                # bm25 devolve valores negativos, mais negativo = melhor. Multiplicar
                # pela confiança empurra o fato duvidoso para perto de zero, ou seja,
                # para o fim da lista.
                consulta_fts = " OR ".join(f'"{t}"' for t in termos)
                linhas = con.execute(
                    "SELECT f.* FROM fatos_fts JOIN fatos f ON f.id = fatos_fts.rowid "
                    "WHERE fatos_fts MATCH ? AND f.confianca > ? "
                    "ORDER BY bm25(fatos_fts) * f.confianca ASC LIMIT ?",
                    (consulta_fts, piso, int(limite)),
                ).fetchall()
            else:
                condicoes = " OR ".join("lower(texto) LIKE ?" for _ in termos)
                parametros = [f"%{t}%" for t in termos] + [piso, int(limite)]
                linhas = con.execute(
                    f"SELECT * FROM fatos WHERE ({condicoes}) AND confianca > ? "
                    "ORDER BY confianca DESC, id DESC LIMIT ?",
                    parametros,
                ).fetchall()

            return [self._montar(con, linha) for linha in linhas]

    def probe(self, entidade: str, limite: int = 20) -> list[Fato]:
        """Tudo que se sabe sobre uma entidade, do mais confiável ao menos."""
        chave = normalize_text(entidade)
        if not chave:
            return []
        with self._lock:
            con = self._connect()
            linhas = con.execute(
                "SELECT f.* FROM fatos f "
                "JOIN fato_entidade fe ON fe.fato_id = f.id "
                "JOIN entidades e ON e.id = fe.entidade_id "
                "WHERE e.chave = ? AND f.confianca > ? "
                "ORDER BY f.confianca DESC, f.id DESC LIMIT ?",
                (chave, PISO_DE_CONFIANCA, int(limite)),
            ).fetchall()
            return [self._montar(con, linha) for linha in linhas]

    def related(self, entidade: str, limite: int = 10) -> list[tuple[str, int]]:
        """Entidades que aparecem nos mesmos fatos, e quantas vezes."""
        chave = normalize_text(entidade)
        if not chave:
            return []
        with self._lock:
            con = self._connect()
            linhas = con.execute(
                "SELECT e2.rotulo AS rotulo, count(*) AS n "
                "FROM entidades e1 "
                "JOIN fato_entidade fe1 ON fe1.entidade_id = e1.id "
                "JOIN fato_entidade fe2 ON fe2.fato_id = fe1.fato_id "
                "JOIN entidades e2 ON e2.id = fe2.entidade_id "
                "WHERE e1.chave = ? AND e2.chave != ? "
                "GROUP BY e2.id ORDER BY n DESC, e2.rotulo LIMIT ?",
                (chave, chave, int(limite)),
            ).fetchall()
            return [(linha["rotulo"], int(linha["n"])) for linha in linhas]

    def contradictions(self, limite: int = 10) -> list[tuple[Fato, Fato]]:
        """Pares candidatos a se contradizer.

        O critério é mecânico: dois fatos que dividem ao menos uma entidade e
        têm sobreposição de palavras, mas texto diferente. Julgar se realmente
        se contradizem é do modelo — SQL não entende negação, ironia nem
        mudança de contexto no tempo.
        """
        with self._lock:
            con = self._connect()
            pares = con.execute(
                "SELECT DISTINCT fe1.fato_id AS a, fe2.fato_id AS b "
                "FROM fato_entidade fe1 "
                "JOIN fato_entidade fe2 ON fe1.entidade_id = fe2.entidade_id "
                "WHERE fe1.fato_id < fe2.fato_id "
                "LIMIT 500"
            ).fetchall()

            candidatos: list[tuple[float, Fato, Fato]] = []
            cache: dict[int, Fato] = {}
            for par in pares:
                primeiro = self._por_id(con, int(par["a"]), cache)
                segundo = self._por_id(con, int(par["b"]), cache)
                if primeiro is None or segundo is None:
                    continue
                sobreposicao = _sobreposicao(primeiro.texto, segundo.texto)
                # Sobreposição alta demais é repetição, baixa demais é assunto
                # diferente. A faixa do meio é onde mora a contradição.
                if 0.3 <= sobreposicao < 0.95:
                    candidatos.append((sobreposicao, primeiro, segundo))

            candidatos.sort(key=lambda item: item[0], reverse=True)
            return [(a, b) for _, a, b in candidatos[:limite]]

    def list_recent(self, limite: int = 20) -> list[Fato]:
        with self._lock:
            con = self._connect()
            linhas = con.execute(
                "SELECT * FROM fatos ORDER BY id DESC LIMIT ?", (int(limite),)
            ).fetchall()
            return [self._montar(con, linha) for linha in linhas]

    def stats(self) -> dict[str, Any]:
        with self._lock:
            con = self._connect()
            return {
                "fatos": int(con.execute("SELECT count(*) AS n FROM fatos").fetchone()["n"]),
                "entidades": int(
                    con.execute("SELECT count(*) AS n FROM entidades").fetchone()["n"]
                ),
                "duvidosos": int(
                    con.execute(
                        "SELECT count(*) AS n FROM fatos WHERE confianca <= ?",
                        (PISO_DE_CONFIANCA,),
                    ).fetchone()["n"]
                ),
                "busca_textual": self._fts,
            }

    # --------------------------------------------------------------- interno

    def _montar(self, con: sqlite3.Connection, linha: sqlite3.Row) -> Fato:
        entidades = [
            item["rotulo"]
            for item in con.execute(
                "SELECT e.rotulo FROM entidades e "
                "JOIN fato_entidade fe ON fe.entidade_id = e.id "
                "WHERE fe.fato_id = ? ORDER BY e.rotulo",
                (linha["id"],),
            ).fetchall()
        ]
        return Fato(
            id=int(linha["id"]),
            texto=linha["texto"],
            confianca=float(linha["confianca"]),
            entidades=entidades,
            fonte=linha["fonte"],
            criado_em=linha["criado_em"],
            confirmacoes=int(linha["confirmacoes"]),
            refutacoes=int(linha["refutacoes"]),
        )

    def _por_id(
        self, con: sqlite3.Connection, fato_id: int, cache: dict[int, Fato]
    ) -> Fato | None:
        if fato_id in cache:
            return cache[fato_id]
        linha = con.execute("SELECT * FROM fatos WHERE id = ?", (fato_id,)).fetchone()
        if linha is None:
            return None
        cache[fato_id] = self._montar(con, linha)
        return cache[fato_id]


# ------------------------------------------------------------------ auxiliares

def _limpar(texto: str) -> str:
    return " ".join(str(texto or "").split())


def _termos(consulta: str) -> list[str]:
    """Palavras da consulta, saneadas para não virarem sintaxe do FTS5.

    O FTS5 tem operadores próprios (`NEAR`, `*`, `-`, `:`). Uma consulta vinda
    do usuário com esses caracteres geraria erro de sintaxe — ou, pior, uma
    busca diferente da pedida.
    """
    palavras = re.findall(r"[\wÀ-ÿ]+", str(consulta or ""), flags=re.UNICODE)
    return [p.lower() for p in palavras if len(p) >= 2][:12]


_VAZIAS = frozenset(
    {"de", "da", "do", "o", "a", "e", "que", "em", "um", "uma", "para", "com",
     "no", "na", "os", "as", "por", "se", "ao", "dos", "das", "e", "ou"}
)


def _sobreposicao(primeiro: str, segundo: str) -> float:
    """Índice de Jaccard entre as palavras de conteúdo dos dois textos."""
    a = {p for p in _termos(primeiro) if p not in _VAZIAS}
    b = {p for p in _termos(segundo) if p not in _VAZIAS}
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)
