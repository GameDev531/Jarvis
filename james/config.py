"""Carregamento e validação da configuração do James.

Regra do projeto: tudo que decide *o que é permitido* mora no config.yaml e é
lido aqui — nunca é inferido pelo LLM em runtime.
"""

from __future__ import annotations

import os
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# webrtcvad só aceita estes valores. Violar isso gera erro obscuro em runtime,
# então validamos no carregamento.
VALID_SAMPLE_RATES = (8000, 16000, 32000, 48000)
VALID_FRAME_MS = (10, 20, 30)


class ConfigError(RuntimeError):
    """Configuração ausente, malformada ou internamente inconsistente."""


def strip_accents(text: str) -> str:
    """Remove acentos preservando o resto. Usado em casamento determinístico."""
    normalized = unicodedata.normalize("NFD", text)
    return "".join(ch for ch in normalized if unicodedata.category(ch) != "Mn")


def normalize_text(text: str) -> str:
    """Minúsculas, sem acento, espaços colapsados. Base de toda comparação."""
    return " ".join(strip_accents(text).lower().split())


@dataclass(frozen=True)
class AudioFormat:
    """Formato PCM usado por TODO o pipeline (captura, VAD, wake word, STT)."""

    sample_rate: int
    channels: int
    sample_width: int
    frame_ms: int

    @property
    def frame_samples(self) -> int:
        """Amostras por frame, por canal."""
        return int(self.sample_rate * self.frame_ms / 1000)

    @property
    def frame_bytes(self) -> int:
        return self.frame_samples * self.channels * self.sample_width

    def ms_to_frames(self, milliseconds: float) -> int:
        """Converte duração em número de frames, arredondando para cima."""
        if milliseconds <= 0:
            return 0
        return max(1, int(round(milliseconds / self.frame_ms)))

    def bytes_to_ms(self, num_bytes: int) -> float:
        bytes_per_second = self.sample_rate * self.channels * self.sample_width
        return num_bytes * 1000.0 / bytes_per_second


class Config:
    """Acesso por caminho pontilhado ao config.yaml, com defaults explícitos."""

    def __init__(self, data: dict[str, Any], path: Path | None = None) -> None:
        self._data = data
        self.path = path
        self.root = PROJECT_ROOT
        self.audio = self._build_audio_format()

    # ------------------------------------------------------------------ acesso

    def get(self, dotted: str, default: Any = None) -> Any:
        """`cfg.get("audio.vad.aggressiveness", 2)`.

        Um valor explicitamente `null` no YAML devolve `default`, porque em YAML
        "não configurei isso" e "configurei como nulo" são a mesma coisa na
        prática para todos os campos deste projeto.
        """
        node: Any = self._data
        for part in dotted.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return default if node is None else node

    def require(self, dotted: str) -> Any:
        value = self.get(dotted, None)
        if value is None:
            raise ConfigError(f"config.yaml: campo obrigatório ausente: '{dotted}'")
        return value

    def section(self, dotted: str) -> dict[str, Any]:
        value = self.get(dotted, {})
        if not isinstance(value, dict):
            raise ConfigError(f"config.yaml: '{dotted}' deveria ser um mapeamento")
        return value

    def resolve_path(self, dotted: str, default: str | None = None) -> Path | None:
        """Resolve um caminho do config relativo à raiz do projeto.

        Devolve None quando o campo está vazio — o chamador decide se isso é
        aceitável (ex: `tts.binary` é opcional, `tts.voice_path` não).
        """
        raw = self.get(dotted, default)
        if not raw:
            return None
        candidate = Path(str(raw)).expanduser()
        if not candidate.is_absolute():
            candidate = self.root / candidate
        return candidate

    # ------------------------------------------------------------- validação

    def _build_audio_format(self) -> AudioFormat:
        fmt = AudioFormat(
            sample_rate=int(self.get("audio.sample_rate", 16000)),
            channels=int(self.get("audio.channels", 1)),
            sample_width=int(self.get("audio.sample_width", 2)),
            frame_ms=int(self.get("audio.frame_ms", 30)),
        )
        if fmt.sample_rate not in VALID_SAMPLE_RATES:
            raise ConfigError(
                f"audio.sample_rate={fmt.sample_rate} inválido. "
                f"webrtcvad aceita apenas {VALID_SAMPLE_RATES}."
            )
        if fmt.frame_ms not in VALID_FRAME_MS:
            raise ConfigError(
                f"audio.frame_ms={fmt.frame_ms} inválido. "
                f"webrtcvad aceita apenas {VALID_FRAME_MS}."
            )
        if fmt.channels != 1:
            raise ConfigError(
                f"audio.channels={fmt.channels}: o pipeline inteiro (VAD, wake word, "
                "whisper.cpp) assume mono. Use 1."
            )
        if fmt.sample_width != 2:
            raise ConfigError(
                f"audio.sample_width={fmt.sample_width}: o pipeline assume PCM 16-bit. Use 2."
            )
        return fmt

    def validate(self) -> list[str]:
        """Checagens não fatais. Devolve lista de avisos legíveis.

        Erros fatais (formato de áudio) já explodem no __init__; aqui ficam as
        coisas que o James consegue contornar mas o usuário deveria saber.
        """
        warnings: list[str] = []

        if not self.section("permissions.apps"):
            warnings.append("permissions.apps está vazio — nenhum app poderá ser aberto.")

        voice = self.resolve_path("tts.voice_path")
        if voice is None:
            warnings.append("tts.voice_path não configurado — o James não terá voz.")
        elif not voice.exists():
            warnings.append(f"Voz do Piper não encontrada em {voice}.")

        binary = self.resolve_path("stt.binary")
        model = self.resolve_path("stt.model")
        if binary is None or not binary.exists():
            warnings.append(
                "stt.binary não configurado — sem whisper.cpp não há modo offline "
                "nem confirmação de ações de risco."
            )
        elif model is None or not model.exists():
            warnings.append(f"Modelo do whisper.cpp não encontrado em {model}.")

        vad = self.section("audio.vad")
        aggressiveness = int(vad.get("aggressiveness", 2))
        if not 0 <= aggressiveness <= 3:
            warnings.append(
                f"audio.vad.aggressiveness={aggressiveness} fora de 0..3; usando 2."
            )

        rpm = int(self.get("llm.rate_limit.requests_per_minute", 10))
        rpd = int(self.get("llm.rate_limit.requests_per_day", 240))
        if rpm <= 0 or rpd <= 0:
            warnings.append("llm.rate_limit com valor <= 0 — o James não faria requisição nenhuma.")

        return warnings

    # ------------------------------------------------------------- utilidades

    def normalized_list(self, dotted: str) -> list[str]:
        """Lista do config normalizada (minúscula, sem acento), sem itens vazios."""
        raw = self.get(dotted, []) or []
        if isinstance(raw, str):
            raw = [raw]
        if not isinstance(raw, Iterable):
            raise ConfigError(f"config.yaml: '{dotted}' deveria ser uma lista")
        return [normalize_text(str(item)) for item in raw if str(item).strip()]

    def app_whitelist(self) -> dict[str, str]:
        """Whitelist de apps com as chaves normalizadas para casamento por voz."""
        result: dict[str, str] = {}
        for spoken, command in self.section("permissions.apps").items():
            if command is None or not str(command).strip():
                continue
            result[normalize_text(str(spoken))] = str(command)
        return result


def _parse_env(texto: str) -> dict[str, str]:
    """Leitor de .env em 15 linhas: `CHAVE=valor`, com # de comentário.

    Existe para o caso em que o `python-dotenv` não está instalado. Sem ele, a
    versão antiga simplesmente não carregava nada — e o efeito era cruel: as
    chaves estavam no arquivo, o James dizia "GEMINI_API_KEY ausente", e não
    havia pista nenhuma ligando uma coisa à outra.

    Um assistente que depende de chave não pode ter o carregamento dela
    condicionado a uma dependência opcional dar certo.
    """
    valores: dict[str, str] = {}
    for linha in texto.splitlines():
        linha = linha.strip()
        if not linha or linha.startswith("#") or "=" not in linha:
            continue
        if linha.startswith("export "):
            linha = linha[len("export "):]
        chave, _, valor = linha.partition("=")
        chave = chave.strip()
        if not chave:
            continue
        valor = valor.strip()
        # Aspas são opcionais no formato; quem escreve à mão costuma usá-las.
        if len(valor) >= 2 and valor[0] == valor[-1] and valor[0] in "\"'":
            valor = valor[1:-1]
        valores[chave] = valor
    return valores


def load_env(root: Path | None = None) -> None:
    """Carrega o .env, se existir. Sem .env o James ainda sobe (modo degradado)."""
    env_path = (root or PROJECT_ROOT) / ".env"
    if not env_path.exists():
        return

    try:
        from dotenv import load_dotenv
    except ImportError:
        pass
    else:
        load_dotenv(env_path, override=False)
        return

    try:
        conteudo = env_path.read_text(encoding="utf-8")
    except OSError:
        return
    # `setdefault`, para casar com o `override=False` acima: uma variável já
    # definida no ambiente real ganha do arquivo. É o que permite trocar uma
    # chave por um turno sem editar o .env.
    for chave, valor in _parse_env(conteudo).items():
        os.environ.setdefault(chave, valor)


def load_config(path: str | Path | None = None) -> Config:
    """Lê o config.yaml e o .env. Levanta ConfigError se o arquivo for inválido."""
    load_env()
    config_path = Path(path) if path else PROJECT_ROOT / "config.yaml"
    if not config_path.exists():
        raise ConfigError(f"config.yaml não encontrado em {config_path}")
    try:
        with config_path.open("r", encoding="utf-8") as handle:
            data = yaml.safe_load(handle)
    except yaml.YAMLError as exc:
        raise ConfigError(f"config.yaml malformado: {exc}") from exc
    if data is None:
        data = {}
    if not isinstance(data, dict):
        raise ConfigError("config.yaml: a raiz deveria ser um mapeamento")
    return Config(data, config_path)


def get_secret(name: str) -> str | None:
    """Lê um segredo do ambiente. Nunca logue o retorno disto."""
    value = os.environ.get(name)
    if value is None:
        return None
    value = value.strip()
    return value or None
