"""Gestos: classificação pura, debounce e liberação da câmera.

O classificador é testado com mãos sintéticas construídas ponto a ponto. É o
único jeito honesto de testar isto sem hardware — e como `classificar` não sabe
o que é câmera, não precisa de mais nada.
"""

import pytest

from james.modes.base import ModeError, ModeState
from james.modes.gestures import (
    ACOES_DE_GESTO,
    Debouncer,
    GestureMode,
    Mao,
    carregar_mapeamento,
    classificar,
    dedos_estendidos,
    tamanho_da_palma,
)

# Índices, para as mãos sintéticas ficarem legíveis.
PULSO = 0
POLEGAR = (1, 2, 3, 4)
INDICADOR = (5, 6, 7, 8)
MEDIO = (9, 10, 11, 12)
ANELAR = (13, 14, 15, 16)
MINIMO = (17, 18, 19, 20)


def montar_mao(dedos: dict[str, bool], polegar_para_cima: bool = True) -> Mao:
    """Mão sintética, palma para cima, com os dedos pedidos estendidos.

    Convenção da imagem: `y` cresce para baixo, então o pulso fica embaixo
    (y grande) e as pontas dos dedos estendidos ficam em cima (y pequeno).
    Um dedo dobrado tem a ponta mais perto do pulso que a articulação do meio,
    que é exatamente o que `dedos_estendidos` mede.
    """
    pontos = [(0.0, 0.0)] * 21
    pontos[PULSO] = (0.5, 0.9)

    # Bases dos dedos, espalhadas na largura da palma.
    bases = {"indicador": 0.40, "medio": 0.50, "anelar": 0.58, "minimo": 0.66}
    grupos = {
        "indicador": INDICADOR,
        "medio": MEDIO,
        "anelar": ANELAR,
        "minimo": MINIMO,
    }
    for nome, indices in grupos.items():
        x = bases[nome]
        mcp, pip, dip, ponta = indices
        pontos[mcp] = (x, 0.70)
        pontos[pip] = (x, 0.58)
        if dedos.get(nome):
            pontos[dip] = (x, 0.48)
            pontos[ponta] = (x, 0.38)      # longe do pulso
        else:
            pontos[dip] = (x, 0.63)
            pontos[ponta] = (x, 0.68)      # dobrado: perto do pulso de novo

    cmc, mcp, ip, ponta = POLEGAR
    pontos[cmc] = (0.44, 0.86)
    pontos[mcp] = (0.36, 0.80)
    if dedos.get("polegar"):
        # Estendido = longe da base do mínimo (x=0.66), do outro lado da palma.
        pontos[ip] = (0.28, 0.76)
        pontos[ponta] = (0.16, 0.62 if polegar_para_cima else 0.98)
    else:
        pontos[ip] = (0.42, 0.76)
        pontos[ponta] = (0.48, 0.72)       # recolhido sobre a palma
    return Mao(tuple(pontos))


PALMA = montar_mao(dict.fromkeys(
    ("polegar", "indicador", "medio", "anelar", "minimo"), True))
PUNHO = montar_mao({})
POLEGAR_CIMA = montar_mao({"polegar": True}, polegar_para_cima=True)
POLEGAR_BAIXO = montar_mao({"polegar": True}, polegar_para_cima=False)
V = montar_mao({"indicador": True, "medio": True})
APONTAR = montar_mao({"indicador": True})


# --------------------------------------------------------------- geometria


def test_mao_precisa_de_21_pontos():
    with pytest.raises(ValueError):
        Mao(((0.0, 0.0),) * 20)


def test_dedos_estendidos_da_palma_aberta():
    assert dedos_estendidos(PALMA) == (True, True, True, True, True)


def test_dedos_estendidos_do_punho():
    assert dedos_estendidos(PUNHO) == (False, False, False, False, False)


def test_tamanho_da_palma_e_positivo():
    assert tamanho_da_palma(PALMA) > 0


# ------------------------------------------------------------ classificação


@pytest.mark.parametrize(
    "mao, esperado",
    [
        (PALMA, "palma_aberta"),
        (PUNHO, "punho"),
        (POLEGAR_CIMA, "polegar_cima"),
        (POLEGAR_BAIXO, "polegar_baixo"),
        (V, "v"),
        (APONTAR, "apontar"),
    ],
)
def test_classificacao(mao, esperado):
    assert classificar(mao) == esperado


def test_polegar_na_horizontal_nao_e_cima_nem_baixo():
    """Sem a margem, um polegar deitado viraria 'cima' por meio pixel."""
    pontos = list(POLEGAR_CIMA.pontos)
    pontos[4] = (0.16, pontos[0][1])   # ponta na mesma altura do pulso
    assert classificar(Mao(tuple(pontos))) is None


def test_mao_pequena_demais_e_ignorada():
    """Mão longe da câmera é ruído, não comando."""
    encolhida = tuple((0.5 + (x - 0.5) * 0.05, 0.5 + (y - 0.5) * 0.05)
                      for x, y in PALMA.pontos)
    assert classificar(Mao(encolhida)) is None


def test_classificacao_e_invariante_a_rotacao():
    """Ninguém segura a mão perfeitamente reta — daí a medida ser radial."""
    import math

    def girar(mao, graus):
        rad = math.radians(graus)
        cx, cy = mao.ponto(0)
        pontos = []
        for x, y in mao.pontos:
            dx, dy = x - cx, y - cy
            pontos.append((
                cx + dx * math.cos(rad) - dy * math.sin(rad),
                cy + dx * math.sin(rad) + dy * math.cos(rad),
            ))
        return Mao(tuple(pontos))

    for graus in (-40, -20, 20, 40):
        assert classificar(girar(PUNHO, graus)) == "punho"
        assert classificar(girar(PALMA, graus)) == "palma_aberta"


def test_posicao_intermediaria_nao_vira_gesto():
    """Chutar um gesto a cada quadro dispararia comandos que ninguém pediu."""
    assert classificar(montar_mao({"indicador": True, "anelar": True})) is None


# ---------------------------------------------------------------- debounce


def test_gesto_precisa_ser_estavel_para_disparar():
    d = Debouncer(estabilidade=3, relogio=lambda: 0.0)
    assert d.alimentar("punho") is None
    assert d.alimentar("punho") is None
    assert d.alimentar("punho") == "punho"


def test_gesto_nao_repete_enquanto_a_mao_fica_parada():
    """Segurar a mão dois segundos não pode aumentar o volume trinta vezes."""
    d = Debouncer(estabilidade=2, relogio=lambda: 0.0)
    disparos = [d.alimentar("punho") for _ in range(30)]
    assert disparos.count("punho") == 1


def test_quadro_solto_nao_dispara():
    d = Debouncer(estabilidade=3, relogio=lambda: 0.0)
    d.alimentar("punho")
    d.alimentar("v")          # ruído de um quadro
    assert d.alimentar("punho") is None


def test_mesmo_gesto_repete_depois_do_descanso():
    agora = [0.0]
    d = Debouncer(estabilidade=1, descanso_s=1.5, relogio=lambda: agora[0])
    assert d.alimentar("polegar_cima") == "polegar_cima"
    d.alimentar(None)         # mão sai de cena
    agora[0] = 2.0
    assert d.alimentar("polegar_cima") == "polegar_cima"


def test_mesmo_gesto_nao_repete_dentro_do_descanso():
    agora = [0.0]
    d = Debouncer(estabilidade=1, descanso_s=1.5, relogio=lambda: agora[0])
    d.alimentar("polegar_cima")
    d.alimentar(None)
    agora[0] = 0.4
    assert d.alimentar("polegar_cima") is None


def test_gesto_diferente_dispara_de_imediato():
    d = Debouncer(estabilidade=1, descanso_s=5.0, relogio=lambda: 0.0)
    assert d.alimentar("punho") == "punho"
    assert d.alimentar("v") == "v"


def test_reset_limpa_o_estado():
    d = Debouncer(estabilidade=2, relogio=lambda: 0.0)
    d.alimentar("punho")
    d.reset()
    assert d.alimentar("punho") is None


# ------------------------------------------------------------- mapeamento


def test_mapeamento_padrao_so_tem_acoes_conhecidas():
    for acao in carregar_mapeamento(None).values():
        assert acao in ACOES_DE_GESTO


def test_acao_inventada_e_descartada():
    """A trava que impede um config errado de dar poder demais a um gesto."""
    mapa = carregar_mapeamento({"punho": "mover_arquivo"})
    assert mapa["punho"] == "parar"          # o padrão sobrevive


def test_gesto_pode_ser_desligado_com_string_vazia():
    assert "punho" not in carregar_mapeamento({"punho": ""})


def test_mapeamento_sobrescreve_o_padrao():
    assert carregar_mapeamento({"punho": "mudo"})["punho"] == "mudo"


# -------------------------------------------------------- o modo em si


class CameraFalsa:
    def __init__(self, quadros=None):
        self.quadros = list(quadros or [object()] * 1000)
        self.liberada = False

    def ler(self):
        return self.quadros.pop(0) if self.quadros else object()

    def liberar(self):
        self.liberada = True


class DetectorFalso:
    def __init__(self, maos=()):
        self.maos = list(maos)
        self.fechado = False

    def detectar(self, quadro, momento_ms):
        return [self.maos.pop(0)] if self.maos else []

    def fechar(self):
        self.fechado = True


def montar_modo(**kwargs):
    camera = kwargs.pop("camera", CameraFalsa())
    detector = kwargs.pop("detector", DetectorFalso())
    acoes = []
    modo = GestureMode(
        on_acao=lambda g, a: acoes.append((g, a)),
        fps=1000.0,
        estabilidade=kwargs.pop("estabilidade", 2),
        timeout_min=kwargs.pop("timeout_min", 0.0),
        camera_factory=lambda: camera,
        detector_factory=lambda: detector,
        **kwargs,
    )
    return modo, camera, detector, acoes


def test_modo_nasce_desligado_e_nao_abre_camera():
    """O ponto todo: construir o modo não custa hardware nenhum."""
    modo, camera, detector, _ = montar_modo()
    assert modo.estado is ModeState.DESLIGADO
    assert camera.liberada is False and detector.fechado is False


def test_desligar_libera_a_camera():
    """A luz do hardware apagar é a única prova visível para o usuário."""
    modo, camera, detector, _ = montar_modo()
    modo.ligar()
    modo.desligar()
    assert camera.liberada is True
    assert detector.fechado is True


def test_camera_que_nao_abre_vira_erro_de_modo():
    modo = GestureMode(
        on_acao=lambda g, a: None,
        camera_factory=lambda: (_ for _ in ()).throw(RuntimeError("em uso")),
        detector_factory=lambda: DetectorFalso(),
    )
    with pytest.raises(ModeError, match="em uso"):
        modo.ligar()
    assert modo.ligado is False


def test_falha_no_detector_libera_a_camera_ja_aberta():
    """Senão a câmera ficaria aberta por causa de um modelo que não carregou."""
    camera = CameraFalsa()
    modo = GestureMode(
        on_acao=lambda g, a: None,
        camera_factory=lambda: camera,
        detector_factory=lambda: (_ for _ in ()).throw(RuntimeError("sem modelo")),
    )
    with pytest.raises(ModeError):
        modo.ligar()
    assert camera.liberada is True


def test_sem_fabricas_o_modo_recusa_ligar():
    modo = GestureMode(on_acao=lambda g, a: None)
    with pytest.raises(ModeError, match="não está configurado"):
        modo.ligar()


def test_gesto_estavel_dispara_a_acao():
    detector = DetectorFalso([PUNHO] * 5)
    modo, _, _, acoes = montar_modo(detector=detector, estabilidade=2)
    modo.ligar()
    for i in range(5):
        modo._passo(i * 100)
    modo.desligar()
    assert ("punho", "parar") in acoes


def test_gesto_sem_acao_mapeada_nao_dispara():
    detector = DetectorFalso([APONTAR] * 5)
    modo, _, _, acoes = montar_modo(detector=detector, estabilidade=1)
    modo.ligar()
    for i in range(5):
        modo._passo(i * 100)
    modo.desligar()
    assert acoes == []


def test_quadro_vazio_nao_quebra_o_laco():
    modo, _, _, acoes = montar_modo(detector=DetectorFalso([]))
    modo.ligar()
    for i in range(3):
        modo._passo(i * 100)
    modo.desligar()
    assert acoes == []


def test_camera_sem_imagem_nao_quebra():
    class Cega(CameraFalsa):
        def ler(self):
            return None

    modo, camera, _, acoes = montar_modo(camera=Cega())
    modo.ligar()
    modo._passo(0)
    modo.desligar()
    assert acoes == []
    assert camera.liberada is True


def test_erro_na_acao_nao_derruba_o_modo():
    """Um gesto que falha não pode deixar a câmera aberta e o laço morto."""
    modo = GestureMode(
        on_acao=lambda g, a: (_ for _ in ()).throw(RuntimeError("falhou")),
        estabilidade=1,
        camera_factory=CameraFalsa,
        detector_factory=lambda: DetectorFalso([PUNHO] * 3),
    )
    modo.ligar()
    modo._passo(0)
    assert modo.ligado is True
    modo.desligar()


def test_desligar_e_idempotente():
    modo, camera, _, _ = montar_modo()
    modo.ligar()
    modo.desligar()
    camera.liberada = False
    modo.desligar()
    assert camera.liberada is False   # não tentou liberar de novo
