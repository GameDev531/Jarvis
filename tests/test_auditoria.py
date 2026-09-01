"""Achados de uma auditoria externa, cada um com o teste que faltava.

O padrão que une quase todos: **uma regra existia em um lugar e não no outro**.
O guard validava a URL; o fetch seguia redirecionamento sem consultá-lo. A
tupla de segredos existia; as chaves de voz entraram depois e não foram
acrescentadas. A validação de cota existia; o YAML mudou de formato e ela
passou a ler uma chave inexistente.

Validar uma vez não é validar sempre, e é por isso que estes testes existem.
"""

from __future__ import annotations

import pytest

from james.config import AudioFormat, Config
from james.security.enderecos import EnderecoBloqueado, validar_host


# --------------------------------- endereço interno: nome resolve para IP


@pytest.mark.parametrize("host", ["127.0.0.1", "10.0.0.1", "192.168.1.1", "169.254.169.254"])
def test_ip_interno_e_bloqueado(host):
    with pytest.raises(EnderecoBloqueado):
        validar_host(host)


def test_nome_que_aponta_para_dentro_e_bloqueado():
    """O buraco original: o guard testava `ipaddress.ip_address(host)`, que
    levanta ValueError num NOME — e o código lia isso como "pode passar".

    `localhost` não estava em lista nenhuma; era só um nome. Qualquer um
    registra um domínio apontando para 127.0.0.1.
    """
    with pytest.raises(EnderecoBloqueado):
        validar_host("localhost")


def test_host_publico_passa():
    validar_host("example.com")


def test_sem_resolver_dns_o_ip_literal_ainda_e_barrado():
    """A resolução custa uma consulta; a checagem literal, não. A segunda não
    pode depender da primeira."""
    with pytest.raises(EnderecoBloqueado):
        validar_host("127.0.0.1", resolver_dns=False)


# ----------------------------------------- redirecionamento valida cada salto


class _RespostaFalsa:
    def __init__(self, status=200, location=None):
        self.status_code = status
        self.headers = {"location": location} if location else {}

    @property
    def is_redirect(self):
        return 300 <= self.status_code < 400 and "location" in self.headers


@pytest.fixture
def dns_permissivo(monkeypatch):
    """Isola o teste de redirecionamento da resolução real.

    Aqui o que está sob teste é a corrente de saltos, não o DNS — e um sandbox
    sem rede reprovaria código correto.
    """
    import james.web.safe_http as mod

    def falso(host, **kwargs):
        from james.security.enderecos import motivo_ip_bloqueado

        motivo = motivo_ip_bloqueado(host)
        if motivo:
            raise EnderecoBloqueado(motivo)
        if host in {"localhost", "interno.local"}:
            raise EnderecoBloqueado(f"'{host}' aponta para dentro")

    monkeypatch.setattr(mod, "validar_host", falso)


class _ClienteFalso:
    def __init__(self, roteiro):
        self.roteiro = list(roteiro)
        self.visitados = []

    def request(self, metodo, url, **kwargs):
        self.visitados.append(url)
        return self.roteiro.pop(0)


def test_redirecionamento_para_dentro_e_bloqueado():
    """O ponto do arquivo inteiro: o guard aprovou a PRIMEIRA url. Uma página
    pública redirecionando para 127.0.0.1 alcançava a interface holográfica, o
    roteador de casa e o serviço de metadados de nuvem."""
    from james.web.safe_http import RedirecionamentoBloqueado, obter

    cliente = _ClienteFalso([_RespostaFalsa(302, "http://127.0.0.1:8080/admin")])
    with pytest.raises(RedirecionamentoBloqueado):
        obter(cliente, "https://exemplo.com")


def test_redirecionamento_para_fora_e_seguido(dns_permissivo):
    """A trava não pode ser tão larga que redirecionamento normal pare de
    funcionar — metade da web encurta URL."""
    from james.web.safe_http import obter

    cliente = _ClienteFalso([
        _RespostaFalsa(301, "https://outro.example.com/pagina"),
        _RespostaFalsa(200),
    ])
    assert obter(cliente, "https://exemplo.com").status_code == 200
    assert len(cliente.visitados) == 2


def test_corrente_infinita_de_redirecionamento_para(dns_permissivo):
    from james.web.safe_http import MAX_SALTOS, RedirecionamentoBloqueado, obter

    cliente = _ClienteFalso(
        [_RespostaFalsa(302, f"https://a{i}.example.com") for i in range(MAX_SALTOS + 2)]
    )
    with pytest.raises(RedirecionamentoBloqueado, match="redirecionamentos"):
        obter(cliente, "https://exemplo.com")


def test_post_vira_get_no_redirecionamento(dns_permissivo):
    """Reenviar o corpo de um POST para outro destino é vazamento de dado."""
    from james.web.safe_http import obter

    class _Espiao(_ClienteFalso):
        def __init__(self, roteiro):
            super().__init__(roteiro)
            self.metodos, self.tinha_corpo = [], []

        def request(self, metodo, url, **kwargs):
            self.metodos.append(metodo)
            self.tinha_corpo.append("data" in kwargs)
            return super().request(metodo, url, **kwargs)

    cliente = _Espiao([_RespostaFalsa(302, "https://outro.example.com"), _RespostaFalsa(200)])
    obter(cliente, "https://exemplo.com", metodo="POST", data={"q": "x"})
    assert cliente.metodos == ["POST", "GET"]
    assert cliente.tinha_corpo == [True, False]


def test_esquema_estranho_e_recusado():
    from james.web.safe_http import RedirecionamentoBloqueado, obter

    cliente = _ClienteFalso([_RespostaFalsa(302, "file:///etc/passwd")])
    with pytest.raises(RedirecionamentoBloqueado):
        obter(cliente, "https://exemplo.com")


# ------------------------------------------------- segredos fora da suíte


def test_a_suite_esconde_TODAS_as_chaves():
    """As duas de voz entraram depois e a tupla não acompanhou.

    A falha aqui é mais cara que nas outras: chave de LLM cobra por requisição,
    chave de voz cobra por CARACTERE. Uma suíte que sintetizasse por engano
    comeria a cota do mês numa rodada.
    """
    from tests.conftest import _CREDENCIAIS

    for nome in ("GEMINI_API_KEY", "OPENROUTER_API_KEY", "PORCUPINE_ACCESS_KEY",
                 "ELEVENLABS_API_KEY", "LMNT_API_KEY"):
        assert nome in _CREDENCIAIS, f"{nome} continuaria visível para os testes"


def test_todo_segredo_do_env_de_exemplo_esta_na_tupla():
    """Trava contra a repetição: se alguém acrescentar uma chave ao
    `.env.example`, o teste cobra a entrada na tupla."""
    from pathlib import Path

    from tests.conftest import _CREDENCIAIS

    exemplo = Path(__file__).resolve().parent.parent / ".env.example"
    chaves = {
        linha.split("=", 1)[0].strip()
        for linha in exemplo.read_text(encoding="utf-8").splitlines()
        if "=" in linha and not linha.strip().startswith("#")
    }
    assert chaves and chaves <= set(_CREDENCIAIS), (
        f"faltam na tupla de conftest: {sorted(chaves - set(_CREDENCIAIS))}"
    )


# ------------------------------------------------------- arredondamento


def test_ms_to_frames_arredonda_para_cima():
    """O docstring dizia "para cima" e o código usava `round()`.

    Com frames de 30 ms, `end_silence_ms: 700` virava 690: o James cortava a
    fala antes do configurado, e sempre para menos. Ficar aquém corta a última
    sílaba de quem fala devagar; passar um pouco custa 30 ms de espera.
    """
    f = AudioFormat(sample_rate=16000, channels=1, sample_width=2, frame_ms=30)
    assert f.ms_to_frames(700) == 24          # era 23 (690 ms)
    assert f.ms_to_frames(400) == 14          # era 13 (390 ms)
    assert f.ms_to_frames(300) == 10          # múltiplo exato, sem mudança
    assert f.ms_to_frames(1) == 1
    assert f.ms_to_frames(0) == 0


def test_a_janela_nunca_fica_menor_que_o_pedido():
    f = AudioFormat(sample_rate=16000, channels=1, sample_width=2, frame_ms=30)
    for ms in range(1, 2000, 7):
        assert f.ms_to_frames(ms) * 30 >= ms


# ------------------------------------------------------------ cota por provedor


def test_cota_invalida_por_provedor_agora_avisa():
    """A validação lia `llm.rate_limit.requests_per_minute`, que o YAML não
    tem — encontrava o padrão, achava tudo bem, e um `requests_per_day: 0`
    passava calado, deixando o James mudo com o aviso desligado."""
    avisos = Config({"llm": {"rate_limit": {"gemini": {"requests_per_day": 0}}}}).validate()
    assert any("gemini" in a and "requests_per_day" in a for a in avisos)


def test_cota_boa_nao_gera_aviso():
    avisos = Config({"llm": {"rate_limit": {
        "gemini": {"requests_per_minute": 10, "requests_per_day": 240},
    }}}).validate()
    assert not any("rate_limit" in a for a in avisos)


def test_aviso_do_whisper_nao_mente_sobre_a_confirmacao():
    """Dizia que sem whisper.cpp não havia confirmação de risco. Havia: o
    diálogo com PIN. Um aviso que exagera treina a pessoa a ignorar avisos."""
    avisos = Config({}).validate()
    stt = [a for a in avisos if "whisper" in a.lower()]
    assert stt and "janela" in stt[0].lower()


# --------------------------------------------------- distribuição limpa


def test_a_distribuicao_recusa_dado_de_execucao():
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from distribuir import _suspeito

    for nome in (".env", ".coverage", "hardware_report.json",
                 "logs/james.log", "state/fatos.db", "memories/USER.md",
                 "james/__pycache__/config.cpython-311.pyc"):
        assert _suspeito(Path(nome)) is not None, f"{nome} entraria no ZIP"


def test_a_distribuicao_deixa_o_codigo_passar():
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from distribuir import _suspeito

    for nome in ("james/config.py", "README.md", "ui/web/app.js", ".env.example"):
        assert _suspeito(Path(nome)) is None, f"{nome} ficaria de fora"


def test_coverage_nao_engana_o_filtro_por_extensao():
    """`Path(".coverage").suffix` é VAZIO — o ponto inicial faz o pathlib
    tratar o nome inteiro como stem. Filtrar por extensão deixava passar, e foi
    assim que ele entrou no repositório."""
    from pathlib import Path

    assert Path(".coverage").suffix == ""      # a armadilha, documentada


# ----------------------------------------------- token fora do arquivo de log


def test_o_token_da_interface_nao_vai_para_o_log():
    """Log vira ZIP, print de tela e anexo de e-mail pedindo ajuda. Token em
    log é token público."""
    from pathlib import Path

    fonte = (Path(__file__).resolve().parent.parent
             / "james" / "ui" / "web_server.py").read_text(encoding="utf-8")
    assert 'logger.info("Interface holográfica em %s", self.url)' not in fonte
    assert "self.porta" in fonte


def test_dns_fora_do_ar_nao_e_chamado_de_ataque():
    """Barrar continua certo: sem saber para onde o nome aponta, não dá para
    garantir que é externo. Mas a frase precisa dizer a verdade."""
    from james.web.safe_http import NomeNaoResolvido, RedirecionamentoBloqueado, obter

    cliente = _ClienteFalso([_RespostaFalsa(200)])
    with pytest.raises(NomeNaoResolvido):
        obter(cliente, "https://este-dominio-nao-existe-mesmo-12345.invalid")
    # Continua sendo um bloqueio para quem só pergunta "posso ir?".
    assert issubclass(NomeNaoResolvido, RedirecionamentoBloqueado)
