"""Motores de palavra de ativação — três caminhos, um contrato.

O Porcupine é bom e leve, mas o console da Picovoice **recusa e-mail pessoal**:
tentar criar conta com Gmail devolve *"Please enter a valid company email"*.
Isso é uma barreira comercial, não técnica, e não deveria decidir se o seu
assistente liga ou não.

Então há três motores, e todos entregam o mesmo contrato:

    sample_rate    int
    frame_length   int      amostras por chamada de process()
    process(x)     int      >= 0 quando detectou
    delete()

| Motor | Conta? | Custo de CPU | Observação |
|---|---|---|---|
| `atalho` | não | **zero** | Sem microfone aberto; aperta a tecla e fala |
| `openwakeword` | não | baixo | ONNX; modelo "hey jarvis" pronto |
| `porcupine` | **sim** | baixo | Precisa de e-mail corporativo |

## Por que `atalho` é o padrão recomendado numa máquina fraca

Os outros dois mantêm o microfone aberto e rodam inferência a cada 30–80 ms,
para sempre. Num Sandy Bridge de 2011 isso é CPU que some do resto do dia. O
atalho custa **nada** enquanto você não aperta — e a mesma tecla que já existe
para o kill switch prova que o mecanismo funciona.

A troca é honesta: você perde o "Jarvis" falado à distância. Ganha uma máquina
que não fica processando áudio a tarde inteira.

## Licença dos modelos do openWakeWord

O código é Apache 2.0, mas os modelos pré-treinados são **CC-BY-NC-SA 4.0** —
uso não comercial. Para um assistente pessoal está tudo certo; se um dia isto
virar produto, será preciso treinar modelos próprios (o projeto suporta).
"""

from __future__ import annotations

from james.logs import get_logger

logger = get_logger("james.wake.motores")


class WakeWordUnavailable(RuntimeError):
    """O motor de palavra de ativação não pôde ser inicializado.

    `falta_pacote` separa "a biblioteca não está instalada" (uma linha de
    `pip` resolve) de "está instalada e quebrou" (aí é problema de verdade).
    A Fase 0 usa essa distinção para não assustar quem só não rodou o install.
    """

    def __init__(self, mensagem: str, *, falta_pacote: bool = False) -> None:
        super().__init__(mensagem)
        self.falta_pacote = falta_pacote


# --------------------------------------------------------------- openWakeWord


class OpenWakeWordEngine:
    """Detecção sem conta, sem chave, sem e-mail corporativo.

    Roda em ONNX Runtime — que no Windows é o único caminho suportado, e que a
    Fase 0 já confirma antes de qualquer coisa.
    """

    nome = "openwakeword"

    # O modelo quer blocos múltiplos de 80 ms. A 16 kHz isso são 1280 amostras.
    # Blocos maiores são mais eficientes e mais lentos para reagir; 80 ms é o
    # menor, e é o que mantém a resposta imediata.
    FRAME_LENGTH = 1280
    SAMPLE_RATE = 16000

    def __init__(
        self,
        modelo: str = "hey_jarvis",
        limiar: float = 0.5,
        vad_threshold: float = 0.0,
    ) -> None:
        try:
            import numpy as np  # noqa: F401
            from openwakeword.model import Model
        except ImportError as exc:
            raise WakeWordUnavailable(
                "Pacote 'openwakeword' não instalado. "
                'Instale com: pip install -e ".[wakeword]"',
                falta_pacote=True,
            ) from exc

        self.sample_rate = self.SAMPLE_RATE
        self.frame_length = self.FRAME_LENGTH
        self.limiar = float(limiar)
        self.modelo_nome = str(modelo)

        try:
            self._baixar_se_preciso()
            opcoes = {"wakeword_models": [self.modelo_nome], "inference_framework": "onnx"}
            if vad_threshold > 0:
                # O VAD embutido descarta silêncio antes da inferência — menos
                # CPU e menos falso positivo com ruído de fundo.
                opcoes["vad_threshold"] = float(vad_threshold)
            self._modelo = Model(**opcoes)
        except WakeWordUnavailable:
            raise
        except Exception as exc:  # noqa: BLE001 — a lib levanta tipos variados
            raise WakeWordUnavailable(
                f"Falha ao carregar o modelo '{modelo}': {exc}"
            ) from exc

        # `predict()` devolve um dicionário com uma pontuação por modelo, e a
        # busca por nome usa `.get(nome, 0.0)`. Se a chave não bater, a nota é
        # sempre zero e a palavra de ativação **nunca dispara** — sem erro, sem
        # log, sem nada. Falhar aqui, na partida, é infinitamente melhor que
        # descobrir isso gritando "Jarvis" para uma máquina muda.
        carregados = list(getattr(self._modelo, "models", {}) or {})
        if carregados and self.modelo_nome not in carregados:
            raise WakeWordUnavailable(
                f"O modelo '{self.modelo_nome}' não está entre os carregados "
                f"({', '.join(carregados)}). Ajuste `wake_word.openwakeword.modelo`."
            )

        logger.info(
            "Palavra de ativação: '%s' (openWakeWord, limiar %.2f)",
            self.modelo_nome, self.limiar,
        )

    @staticmethod
    def _baixar_se_preciso() -> None:
        """Baixa os modelos pré-treinados na primeira execução.

        São poucos megabytes e ficam em cache. Sem rede na primeira vez, o erro
        aparece aqui — com a causa clara — em vez de virar um modelo ausente
        num traceback obscuro lá dentro.
        """
        import openwakeword

        try:
            openwakeword.utils.download_models()
        except Exception as exc:  # noqa: BLE001
            raise WakeWordUnavailable(
                f"Não consegui baixar os modelos do openWakeWord: {exc}. "
                "É preciso internet na primeira execução."
            ) from exc

    def process(self, samples) -> int:
        """>= 0 quando detectou. Mesma convenção do Porcupine."""
        try:
            resultado = self._modelo.predict(samples)
        except Exception as exc:  # noqa: BLE001 — quadro ruim não derruba a escuta
            logger.debug("Erro na inferência da palavra de ativação: %s", exc)
            return -1

        pontuacao = float(resultado.get(self.modelo_nome, 0.0))
        if pontuacao < self.limiar:
            return -1
        # Sem isto, os quadros seguintes continuariam acima do limiar e a
        # detecção dispararia várias vezes para uma única palavra falada.
        self._modelo.reset()
        logger.debug("Detecção com pontuação %.2f", pontuacao)
        return 0

    def delete(self) -> None:
        self._modelo = None


# ------------------------------------------------------------------ Porcupine


class PorcupineEngine:
    """O caminho original. Exige conta com e-mail corporativo."""

    nome = "porcupine"

    def __init__(
        self,
        access_key: str,
        keyword: str = "jarvis",
        keyword_path=None,
        sensitivity: float = 0.6,
    ) -> None:
        try:
            import pvporcupine
        except ImportError as exc:
            raise WakeWordUnavailable(
                "Pacote 'pvporcupine' não instalado. "
                'Instale com: pip install -e ".[porcupine]" — ou use '
                "`wake_word.motor: openwakeword`, que não exige conta.",
                falta_pacote=True,
            ) from exc

        if not access_key:
            raise WakeWordUnavailable(
                "Chave do Porcupine ausente. O console da Picovoice recusa "
                "e-mail pessoal; se isso travar você, use "
                "`wake_word.motor: openwakeword` ou `atalho` no config.yaml."
            )

        try:
            if keyword_path is not None:
                if not keyword_path.exists():
                    raise WakeWordUnavailable(
                        f"Modelo de palavra de ativação não encontrado: {keyword_path}"
                    )
                logger.info("Palavra de ativação personalizada: %s", keyword_path.name)
                self._motor = pvporcupine.create(
                    access_key=access_key,
                    keyword_paths=[str(keyword_path)],
                    sensitivities=[float(sensitivity)],
                )
            else:
                logger.info("Palavra de ativação: '%s' (Porcupine)", keyword)
                self._motor = pvporcupine.create(
                    access_key=access_key,
                    keywords=[str(keyword).lower()],
                    sensitivities=[float(sensitivity)],
                )
        except WakeWordUnavailable:
            raise
        except Exception as exc:  # noqa: BLE001 — o SDK levanta tipos variados
            raise WakeWordUnavailable(f"Falha ao inicializar o Porcupine: {exc}") from exc

        self.sample_rate = self._motor.sample_rate
        self.frame_length = self._motor.frame_length

    def process(self, samples) -> int:
        return self._motor.process(samples)

    def delete(self) -> None:
        self._motor.delete()


# --------------------------------------------------------------------- atalho


class HotkeyEngine:
    """Sem palavra de ativação: você aperta uma tecla e fala.

    Não é um motor de áudio — é a ausência de um. O microfone só abre depois do
    atalho, então **nada é processado enquanto você não pede**. Numa máquina
    modesta essa é a diferença entre um assistente que existe de fundo e um que
    come CPU a tarde inteira.

    O `WakeListener` reconhece esta classe e pula o laço de escuta por completo.
    """

    nome = "atalho"
    sample_rate = 16000
    frame_length = 512          # nunca usado; existe só para o contrato

    def __init__(self, atalho: str = "ctrl+alt+espaco") -> None:
        self.atalho = str(atalho)
        logger.info("Palavra de ativação desligada. Use %s para falar.", self.atalho)

    def process(self, samples) -> int:  # pragma: no cover - nunca chamado
        return -1

    def delete(self) -> None:
        pass


# ------------------------------------------------------------------- fábrica


def build_wake_engine(config, get_secret):
    """Monta o motor escolhido no `config.yaml`. Levanta WakeWordUnavailable.

    Sem `motor` definido, tenta na ordem: openwakeword (sem conta) → porcupine.
    O atalho nunca é escolhido sozinho porque muda a forma de usar o James, e
    isso é decisão de quem usa, não do código.
    """
    escolhido = str(config.get("wake_word.motor", "") or "").strip().lower()

    if escolhido == "atalho":
        return HotkeyEngine(str(config.get("wake_word.atalho", "ctrl+alt+espaco")))

    if escolhido == "porcupine":
        return _porcupine(config, get_secret)

    if escolhido == "openwakeword":
        return _openwakeword(config)

    if escolhido:
        raise WakeWordUnavailable(
            f"Motor de palavra de ativação desconhecido: {escolhido!r}. "
            "Use 'openwakeword', 'porcupine' ou 'atalho'."
        )

    # Sem escolha explícita: o que não exige conta vem primeiro.
    erros = []
    falhas = []
    for tentativa in (_openwakeword, lambda c: _porcupine(c, get_secret)):
        try:
            return tentativa(config)
        except WakeWordUnavailable as exc:
            erros.append(str(exc))
            falhas.append(exc)
    raise WakeWordUnavailable(
        "Nenhum motor de palavra de ativação disponível.\n  " + "\n  ".join(erros)
        + "\n\nAlternativa sem instalar nada: `wake_word.motor: atalho` no config.yaml.",
        # Só é "falta instalar" se *nenhum* motor chegou a rodar de verdade.
        # Um deles instalado e quebrado é outro problema, e merece outro texto.
        falta_pacote=all(getattr(exc, "falta_pacote", False) for exc in falhas),
    )


def _openwakeword(config):
    secao = config.section("wake_word.openwakeword")
    return OpenWakeWordEngine(
        modelo=str(secao.get("modelo", "hey_jarvis")),
        limiar=float(secao.get("limiar", 0.5)),
        vad_threshold=float(secao.get("vad", 0.0)),
    )


def _porcupine(config, get_secret):
    return PorcupineEngine(
        access_key=get_secret(
            str(config.get("wake_word.access_key_env", "PORCUPINE_ACCESS_KEY"))
        ),
        keyword=str(config.get("wake_word.keyword", "jarvis")),
        keyword_path=config.resolve_path("wake_word.keyword_path"),
        sensitivity=float(config.get("wake_word.sensitivity", 0.6)),
    )
