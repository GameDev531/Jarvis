"""Snapshot binding contra páginas que mudam de verdade.

Um dublê de página sempre concorda com o que o teste espera dele. O que
precisa ser provado aqui é o contrário: que uma página REAL, que navegou,
recarregou ou trocou o conteúdo por baixo, faz a ação ser recusada.

O caso que motivou tudo isto: entre inspecionar e clicar passam segundos, e o
seletor `button:nth-of-type(2)` continua casando — com outro botão.

    pytest -m browser
"""

from __future__ import annotations

import pytest

from james.browser.actions import AcaoRecusada, preencher
from james.browser.snapshot import (
    CODIGO_PAGINA_MUDOU,
    SnapshotInvalido,
    Snapshots,
    agora,
    capturar,
    revalidar,
)

pytestmark = pytest.mark.browser

pw = pytest.importorskip("playwright.sync_api", reason="playwright não instalado")


FORMULARIO = """
<!doctype html><html lang="pt-BR"><head><title>Entrar</title></head><body>
<h1>Entrar</h1>
<form>
  <label for="em">E-mail</label><input id="em" type="text" name="email">
  <label for="pw">Senha</label><input id="pw" type="password" name="senha">
  <label for="cc">Cartão</label><input id="cc" type="text" name="card_number">
  <input id="oculto" type="hidden" name="csrf" value="x">
  <button id="ok" type="button">Confirmar</button>
  <button id="cancelar" type="button">Cancelar</button>
</form>
</body></html>
"""

LISTA = """
<!doctype html><html lang="pt-BR"><head><title>Lista</title></head><body>
<h1>Itens</h1><div id="alvo"><button class="acao">Excluir</button></div>
<script>
  window.trocar = () => {
    document.getElementById('alvo').innerHTML =
      '<button class="acao">Comprar</button>';
  };
  window.duplicar = () => {
    document.getElementById('alvo').innerHTML =
      '<button class="acao">Excluir</button><button class="acao">Excluir</button>';
  };
</script>
</body></html>
"""


@pytest.fixture
def pagina(navegador):        # `navegador` vem do conftest desta pasta
    page = navegador.new_page()
    yield page
    page.close()


def com(pagina, html):
    pagina.set_content(html)
    return capturar(pagina, "1", agora())


# ------------------------------------------------------------- o caminho feliz


def test_o_snapshot_cataloga_o_que_da_para_operar(pagina):
    snap = com(pagina, FORMULARIO)
    papeis = {e.papel for e in snap.elementos.values()}
    assert "button" in papeis and "input" in papeis

    nomes = {e.nome for e in snap.elementos.values()}
    assert "Confirmar" in nomes and "Cancelar" in nomes


def test_campo_oculto_nao_entra_no_catalogo(pagina):
    """O que não dá para ver não devia nem ser oferecido ao modelo."""
    snap = com(pagina, FORMULARIO)
    assert all(e.tipo != "hidden" for e in snap.elementos.values())


def test_agir_logo_depois_de_inspecionar_funciona(pagina):
    """A contraprova: se tudo fosse recusado, os testes abaixo não valeriam."""
    snap = com(pagina, FORMULARIO)
    email = next(e for e in snap.elementos.values() if e.nome == "E-mail")
    estado = revalidar(pagina, snap, email, agora())
    assert estado["quantos"] == 1
    assert preencher(pagina, email.seletor, "ana@exemplo.com", estado=estado)
    assert pagina.input_value("#em") == "ana@exemplo.com"


# ------------------------------------------------- as seis conferências


def test_recarregar_a_pagina_invalida_a_leitura(pagina):
    """A marca posta na janela some com o documento. É o caso que ela pega
    com certeza."""
    snap = com(pagina, FORMULARIO)
    alvo = next(iter(snap.elementos.values()))
    pagina.set_content(FORMULARIO)          # documento novo, mesmo HTML

    with pytest.raises(SnapshotInvalido) as erro:
        revalidar(pagina, snap, alvo, agora())
    assert erro.value.codigo == CODIGO_PAGINA_MUDOU


def test_navegar_para_outra_origem_invalida(pagina):
    """A mais grave: outro site, outras regras, e um clique 'no mesmo lugar'
    acontece num domínio que ninguém autorizou."""
    pagina.goto("data:text/html,<h1>a</h1><button id=b>Ok</button>")
    snap = capturar(pagina, "1", agora())
    alvo = next(iter(snap.elementos.values()))

    pagina.goto("about:blank")
    with pytest.raises(SnapshotInvalido):
        revalidar(pagina, snap, alvo, agora())


def test_o_elemento_trocar_de_nome_no_mesmo_lugar_invalida(pagina):
    """O caso que a marca de documento NÃO pega, e que motivou tudo.

    O app troca o conteúdo sem navegar: o seletor continua casando, a marca
    continua lá, e o botão que dizia "Excluir" agora diz "Comprar". Sem a
    revalidação do elemento, o James clicaria em comprar achando que exclui.
    """
    snap = com(pagina, LISTA)
    botao = next(e for e in snap.elementos.values() if e.nome == "Excluir")

    pagina.evaluate("window.trocar()")
    with pytest.raises(SnapshotInvalido) as erro:
        revalidar(pagina, snap, botao, agora())
    assert "Comprar" in str(erro.value)


def test_seletor_que_passa_a_casar_com_dois_e_recusado(pagina):
    """Ambiguidade nunca vira escolha: com dois candidatos, "o primeiro" é uma
    moeda jogada para o alto sobre uma ação irreversível."""
    snap = com(pagina, LISTA)
    botao = next(e for e in snap.elementos.values() if e.nome == "Excluir")

    pagina.evaluate("window.duplicar()")
    with pytest.raises(SnapshotInvalido) as erro:
        revalidar(pagina, snap, botao, agora())
    assert "ambígu" in str(erro.value).lower() or "2" in str(erro.value)


def test_elemento_que_sumiu_e_recusado(pagina):
    snap = com(pagina, LISTA)
    botao = next(e for e in snap.elementos.values() if e.nome == "Excluir")
    pagina.evaluate("document.getElementById('alvo').innerHTML = ''")

    with pytest.raises(SnapshotInvalido):
        revalidar(pagina, snap, botao, agora())


def test_elemento_desabilitado_e_recusado(pagina):
    snap = com(pagina, FORMULARIO)
    ok = next(e for e in snap.elementos.values() if e.nome == "Confirmar")
    pagina.evaluate("document.getElementById('ok').disabled = true")

    with pytest.raises(SnapshotInvalido) as erro:
        revalidar(pagina, snap, ok, agora())
    assert "desabilitado" in str(erro.value)


def test_elemento_escondido_e_recusado(pagina):
    snap = com(pagina, FORMULARIO)
    ok = next(e for e in snap.elementos.values() if e.nome == "Confirmar")
    pagina.evaluate("document.getElementById('ok').style.display = 'none'")

    with pytest.raises(SnapshotInvalido):
        revalidar(pagina, snap, ok, agora())


def test_leitura_velha_e_recusada(pagina):
    from james.browser import snapshot as mod

    snap = com(pagina, FORMULARIO)
    alvo = next(iter(snap.elementos.values()))
    with pytest.raises(SnapshotInvalido) as erro:
        revalidar(pagina, snap, alvo, agora() + mod.VALIDADE_S + 1)
    assert "segundos" in str(erro.value)


# ------------------------------------- as travas de campo, contra DOM real


def test_a_trava_de_senha_vale_pelo_caminho_do_snapshot(pagina):
    """A trava não pode existir só no caminho antigo.

    `preencher` agora recebe o estado que a revalidação leu. Se as chaves dos
    dois caminhos não batessem, a conferência de tipo olharia um dicionário
    sem o campo `tipo` — e passaria.
    """
    snap = com(pagina, FORMULARIO)
    senha = next(e for e in snap.elementos.values() if e.nome == "Senha")
    estado = revalidar(pagina, snap, senha, agora())

    with pytest.raises(AcaoRecusada):
        preencher(pagina, senha.seletor, "123456", estado=estado)
    assert pagina.input_value("#pw") == ""


def test_a_trava_de_cartao_vale_pelo_mesmo_caminho(pagina):
    """Aqui o tipo é `text`: quem denuncia é o atributo `name`, que só chega
    por `nome_campo` — a chave que faltava na lista de conferência."""
    snap = com(pagina, FORMULARIO)
    cartao = next(e for e in snap.elementos.values() if e.nome == "Cartão")
    estado = revalidar(pagina, snap, cartao, agora())

    with pytest.raises(AcaoRecusada):
        preencher(pagina, cartao.seletor, "4111111111111111", estado=estado)
    assert pagina.input_value("#cc") == ""


# --------------------------------------------------------------- o registro


def test_snapshot_de_outra_aba_e_recusado(pagina):
    """Cruzar leitura de uma aba com ação em outra é o erro de alvo da Fase B
    disfarçado — e seria fácil de cometer com duas abas parecidas."""
    guarda = Snapshots()
    guarda.guardar(com(pagina, FORMULARIO))
    snap_id = next(iter(guarda._por_id))

    with pytest.raises(SnapshotInvalido) as erro:
        guarda.exigir(snap_id, "2")
    assert "aba" in str(erro.value)


def test_inspecionar_de_novo_aposenta_a_leitura_anterior(pagina):
    """Guardar histórico convidaria o modelo a agir sobre uma leitura antiga
    porque ela 'ainda estava na conversa'."""
    guarda = Snapshots()
    primeiro = com(pagina, FORMULARIO)
    guarda.guardar(primeiro)
    guarda.guardar(com(pagina, FORMULARIO))

    with pytest.raises(SnapshotInvalido):
        guarda.exigir(primeiro.snapshot_id, "1")
