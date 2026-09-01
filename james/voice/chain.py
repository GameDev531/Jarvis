"""Cadeia de vozes — nuvem primeiro, local como reserva.

Mesmo desenho que `llm.roles` já usa para os modelos: uma lista em ordem de
preferência, e quem estiver de pé assume. A diferença é que aqui a troca é
audível — você percebe pela voz qual motor está falando, o que é um indicador
melhor que qualquer log.

    voz:
      cadeia: [elevenlabs, piper]     # padrão: nuvem, com o local de reserva
      cadeia: [piper]                 # só local, para quem quer tudo offline

## Por que a cadeia é por FRASE, e não por sessão

O `Speaker` divide a resposta em sentenças e sintetiza uma a uma. Se a cota
acabasse no meio, uma decisão por sessão deixaria a fala pela metade. Decidir
por frase faz a troca acontecer no limite entre duas sentenças — que é onde
uma pausa já existe naturalmente, e por isso quase não se nota.

## O que a cadeia NÃO faz

Não tenta a nuvem quando o orçamento não comporta o texto. Perguntar antes
(`budget.cabe`) em vez de tentar e falhar evita gastar caracteres numa
requisição que vai ser recusada — e evita a latência da ida e volta.

## Quando tudo falha

O James fica mudo, e isso é aceitável: a interface continua mostrando o texto,
e o log diz o motivo. Ficar mudo com o motivo escrito é melhor que travar o
turno tentando uma quarta alternativa que também não existe.
"""

from __future__ import annotations

import threading

from james.logs import audit, get_logger
from james.voice.tts import TTSUnavailable

logger = get_logger("james.voice.cadeia")

# Depois de uma falha, o motor fica de castigo por este tempo. Sem isso, uma
# rede instável faria cada frase pagar o timeout da nuvem antes de cair para o
# Piper — e a resposta inteira ficaria arrastada.
_CASTIGO_S = 120.0


class _Motor:
    """Um motor na cadeia, com o castigo que ele leva ao falhar."""

    def __init__(self, nome: str, tts, orcamento=None) -> None:
        self.nome = nome
        self.tts = tts
        self.orcamento = orcamento
        self.livre_em = 0.0

    def disponivel(self, agora: float, texto: str) -> bool:
        if agora < self.livre_em:
            return False
        if self.orcamento is not None and not self.orcamento.cabe(texto):
            return False
        return True


class VoiceChain:
    """Expõe o mesmo contrato de um TTS: `synthesize()` e `sample_rate`."""

    def __init__(self, motores: list, clock=None) -> None:
        import time as _time

        self._motores = [m for m in motores if m is not None]
        self._clock = clock or _time.monotonic
        self._lock = threading.RLock()
        self._ultimo = self._motores[0] if self._motores else None

    @property
    def disponivel(self) -> bool:
        return bool(self._motores)

    @property
    def sample_rate(self) -> int:
        """A taxa do motor que falou por último.

        Importa porque os motores diferem: a ElevenLabs entrega 16 kHz e o
        Piper costuma entregar 22050. O reprodutor recebe a taxa junto com o
        PCM a cada frase, então a troca no meio da resposta funciona — desde
        que este valor acompanhe quem realmente sintetizou.
        """
        with self._lock:
            if self._ultimo is not None:
                return int(getattr(self._ultimo.tts, "sample_rate", 22050))
            return 22050

    @property
    def backend(self) -> str:
        nomes = [m.nome for m in self._motores]
        return " -> ".join(nomes) if nomes else "indisponível"

    # ---------------------------------------------------------------- síntese

    def synthesize(self, text: str) -> bytes:
        limpo = " ".join(str(text or "").split())
        if not limpo:
            return b""
        if not self._motores:
            raise TTSUnavailable("Nenhum motor de voz disponível.")

        agora = self._clock()
        erros: list[str] = []

        for motor in self._motores:
            if not motor.disponivel(agora, limpo):
                continue
            try:
                pcm = motor.tts.synthesize(limpo)
            except TTSUnavailable as exc:
                erros.append(f"{motor.nome}: {exc}")
                motor.livre_em = agora + _CASTIGO_S
                logger.warning(
                    "Voz '%s' falhou (%s). Fora por %.0fs; tentando a próxima.",
                    motor.nome, exc, _CASTIGO_S,
                )
                audit("voz_falhou", motor=motor.nome, erro=str(exc)[:120])
                continue
            except Exception as exc:  # noqa: BLE001 — motor externo, tipo variado
                erros.append(f"{motor.nome}: {exc}")
                motor.livre_em = agora + _CASTIGO_S
                logger.exception("Erro inesperado na voz '%s'.", motor.nome)
                continue

            if not pcm:
                continue

            # Só cobra depois de o áudio chegar de verdade. Cobrar antes faria
            # uma falha de rede consumir cota que a ElevenLabs não cobrou.
            if motor.orcamento is not None:
                motor.orcamento.consumir(limpo)

            with self._lock:
                if self._ultimo is not motor:
                    logger.info("Voz agora é '%s'.", motor.nome)
                self._ultimo = motor
            return pcm

        raise TTSUnavailable(
            "Nenhum motor de voz respondeu. " + " | ".join(erros)
            if erros
            else "Nenhum motor de voz disponível para este texto."
        )

    def prewarm(self) -> float:
        """Aquece só o motor local: a nuvem não tem o que carregar."""
        for motor in self._motores:
            aquecer = getattr(motor.tts, "prewarm", None)
            if aquecer is None or motor.orcamento is not None:
                continue
            try:
                return float(aquecer())
            except Exception as exc:  # noqa: BLE001 — aquecer nunca é crítico
                logger.warning("Falha ao aquecer '%s': %s", motor.nome, exc)
        return 0.0

    def resumo(self) -> dict:
        """Estado de cada motor, para a interface mostrar."""
        agora = self._clock()
        return {
            "motores": [
                {
                    "nome": m.nome,
                    "de_castigo": agora < m.livre_em,
                    "orcamento": m.orcamento.resumo() if m.orcamento else None,
                }
                for m in self._motores
            ],
            "atual": self._ultimo.nome if self._ultimo else None,
        }


def build_voice_chain(config, state_dir=None) -> VoiceChain:
    """Monta a cadeia a partir do `config.yaml`. Nunca levanta.

    Um motor que não sobe é registrado e pulado — a cadeia existe justamente
    para que a ausência de um não impeça o outro.
    """
    from pathlib import Path

    from james.config import get_secret

    ordem = [str(n).strip().lower() for n in (config.get("voz.cadeia") or ["piper"])]
    motores: list[_Motor] = []

    for nome in ordem:
        if nome == "elevenlabs":
            motor = _montar_elevenlabs(config, get_secret, Path(state_dir) if state_dir else None)
        elif nome == "lmnt":
            motor = _montar_lmnt(config, get_secret, Path(state_dir) if state_dir else None)
        elif nome == "piper":
            motor = _montar_piper(config)
        else:
            logger.warning("Motor de voz desconhecido no config: %r", nome)
            continue
        if motor is not None:
            motores.append(motor)

    if not motores:
        logger.error("Nenhum motor de voz disponível — o James ficará mudo.")
    else:
        logger.info("Cadeia de voz: %s", " -> ".join(m.nome for m in motores))
    return VoiceChain(motores)


def _montar_elevenlabs(config, get_secret, state_dir):
    from james.voice.budget import CARACTERES_GRATIS_POR_MES, CharacterBudget
    from james.voice.elevenlabs_tts import MODELO_PADRAO, VOZ_PADRAO, ElevenLabsTTS

    chave = get_secret("ELEVENLABS_API_KEY")
    if not chave:
        logger.warning("ELEVENLABS_API_KEY ausente — a voz na nuvem fica de fora.")
        return None

    secao = config.section("voz.elevenlabs")
    try:
        tts = ElevenLabsTTS(
            api_key=chave,
            voice_id=str(secao.get("voice_id", "") or "").strip() or VOZ_PADRAO,
            model_id=str(secao.get("modelo", "") or "").strip() or MODELO_PADRAO,
            timeout_s=float(secao.get("timeout_s", 30)),
            estabilidade=float(secao.get("estabilidade", 0.5)),
            similaridade=float(secao.get("similaridade", 0.75)),
            velocidade=float(secao.get("velocidade", 1.0)),
        )
    except TTSUnavailable as exc:
        logger.warning("Voz na nuvem indisponível: %s", exc)
        return None

    orcamento = CharacterBudget(
        limite_mensal=int(secao.get("caracteres_por_mes", CARACTERES_GRATIS_POR_MES)),
        state_path=(state_dir / "voz_orcamento.json") if state_dir else None,
        dia_da_virada=int(secao.get("dia_da_virada", 1)),
    )
    return _Motor("elevenlabs", tts, orcamento)


def _montar_lmnt(config, get_secret, state_dir):
    """Plano C: mais 15.000 caracteres/mês, de outra conta e outra contagem.

    Sem este degrau, o dia em que a cota da ElevenLabs acaba é o dia em que a
    voz cai de uma vez para o Piper local. Cada motor tem o próprio orçamento
    e o próprio arquivo de estado — somar as duas cotas num balde só faria uma
    apagar a outra na virada do mês.
    """
    from james.voice.budget import CharacterBudget
    from james.voice.lmnt_tts import (
        CARACTERES_GRATIS_POR_MES_LMNT,
        ENDPOINT_PADRAO,
        MODELO_PADRAO,
        VOZ_PADRAO,
        LmntTTS,
    )

    chave = get_secret("LMNT_API_KEY")
    if not chave:
        logger.info("LMNT_API_KEY ausente — o plano C da voz fica de fora.")
        return None

    secao = config.section("voz.lmnt")
    try:
        tts = LmntTTS(
            api_key=chave,
            voice_id=str(secao.get("voz", "") or "").strip() or VOZ_PADRAO,
            model_id=str(secao.get("modelo", "") or "").strip() or MODELO_PADRAO,
            endpoint=str(secao.get("endpoint", "") or "").strip() or ENDPOINT_PADRAO,
            timeout_s=float(secao.get("timeout_s", 30)),
            idioma=str(secao.get("idioma", "pt")),
        )
    except TTSUnavailable as exc:
        logger.warning("LMNT indisponível: %s", exc)
        return None

    orcamento = CharacterBudget(
        limite_mensal=int(secao.get("caracteres_por_mes", CARACTERES_GRATIS_POR_MES_LMNT)),
        # Arquivo próprio: a cota da LMNT não é a da ElevenLabs.
        state_path=(state_dir / "voz_orcamento_lmnt.json") if state_dir else None,
        dia_da_virada=int(secao.get("dia_da_virada", 1)),
    )
    return _Motor("lmnt", tts, orcamento)


def _montar_piper(config):
    from james.voice.tts import PiperTTS

    try:
        tts = PiperTTS(
            voice_path=config.resolve_path("tts.voice_path"),
            binary=config.resolve_path("tts.binary"),
            length_scale=float(config.get("tts.length_scale", 1.0)),
        )
    except TTSUnavailable as exc:
        logger.warning("Voz local indisponível: %s", exc)
        return None
    return _Motor("piper", tts)
