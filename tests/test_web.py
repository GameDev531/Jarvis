"""Extração de texto e leitura de resultados de busca — sem rede."""

from james.web.extract import extract_text, extract_title
from james.web.search import _desembrulhar, parse_results, termos_relevantes

PAGINA = """
<html><head><title>  Título   da Página </title>
<style>body { color: red }</style></head>
<body>
<nav><a href="/">Início</a><a href="/sobre">Sobre</a></nav>
<header>Cabeçalho do site</header>
<article>
  <h1>Assunto principal</h1>
  <p>Primeiro parágrafo com conteúdo de verdade.</p>
  <p>Segundo parágrafo, também relevante.</p>
</article>
<script>console.log('rastreador')</script>
<footer>Todos os direitos reservados</footer>
</body></html>
"""


def test_titulo_e_extraido():
    assert extract_title(PAGINA) == "Título da Página"


def test_texto_do_conteudo_e_extraido():
    texto = extract_text(PAGINA)
    assert "Primeiro parágrafo com conteúdo de verdade." in texto
    assert "Segundo parágrafo" in texto


def test_script_e_style_ficam_de_fora():
    """Código dentro do contexto do modelo é ruído, ou instrução disfarçada."""
    texto = extract_text(PAGINA)
    assert "console.log" not in texto
    assert "color: red" not in texto


def test_menu_e_rodape_ficam_de_fora():
    """Repetem em toda página do site e afogariam o conteúdo real."""
    texto = extract_text(PAGINA)
    assert "Todos os direitos reservados" not in texto
    assert "Cabeçalho do site" not in texto


def test_paragrafos_sao_preservados():
    texto = extract_text("<p>Primeiro trecho aqui</p><p>Segundo trecho aqui</p>")
    assert "\n" in texto


def test_entidades_html_sao_decodificadas():
    assert "ação" in extract_text("<p>uma a&ccedil;&atilde;o qualquer</p>")


def test_texto_gigante_e_truncado():
    grande = "<p>" + ("palavra " * 5000) + "</p>"
    texto = extract_text(grande, max_chars=500)
    assert "truncada" in texto
    assert len(texto) < 600


def test_html_quebrado_nao_derruba():
    """HTML malformado é a regra, não a exceção."""
    for quebrado in ("<p>sem fechar", "</div></div>", "<<>>", ""):
        extract_text(quebrado)


def test_tag_fechada_sem_abrir_nao_trava_o_filtro():
    """Um </script> solto não pode fazer o filtro engolir o resto da página."""
    texto = extract_text("</script><p>conteúdo que precisa aparecer aqui</p>")
    assert "conteúdo que precisa aparecer" in texto


def test_pagina_sem_titulo():
    assert extract_title("<html><body>x</body></html>") == ""


# ------------------------------------------------------------ resultados

RESULTADOS = """
<div class="result">
  <a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexemplo.com%2Fartigo">
    Título do primeiro resultado</a>
  <a class="result__snippet">Resumo do primeiro resultado.</a>
</div>
<div class="result">
  <a class="result__a" href="https://outro.com/pagina">Segundo resultado</a>
  <a class="result__snippet">Resumo do segundo.</a>
</div>
"""


def test_resultados_sao_lidos():
    resultados = parse_results(RESULTADOS)
    assert len(resultados) == 2
    assert resultados[0].titulo == "Título do primeiro resultado"
    assert resultados[0].resumo == "Resumo do primeiro resultado."


def test_link_do_redirecionador_e_desembrulhado():
    """Sem isso, todo resultado apontaria para o próprio buscador."""
    assert parse_results(RESULTADOS)[0].url == "https://exemplo.com/artigo"


def test_link_direto_passa_intacto():
    assert parse_results(RESULTADOS)[1].url == "https://outro.com/pagina"


def test_desembrulhar_lida_com_formatos_variados():
    assert _desembrulhar("") == ""
    assert _desembrulhar("https://direto.com") == "https://direto.com"
    assert _desembrulhar("//site.com/x") == "https://site.com/x"


def test_url_repetida_e_descartada():
    """Variedade de fonte vale mais que repetição numa pesquisa."""
    duplicado = RESULTADOS + RESULTADOS
    assert len(parse_results(duplicado, limite=10)) == 2


def test_limite_de_resultados():
    assert len(parse_results(RESULTADOS, limite=1)) == 1


def test_html_sem_resultados():
    assert parse_results("<html><body>nada aqui</body></html>") == []


def test_resultado_sem_titulo_e_ignorado():
    html = '<a class="result__a" href="https://x.com"></a>'
    assert parse_results(html) == []


# --------------------------------------------------------------- termos

def test_termos_mais_frequentes():
    texto = "reator arco reator arco reator palladium"
    termos = termos_relevantes(texto, limite=2)
    assert termos[0] == "reator"


def test_palavras_vazias_sao_descartadas():
    termos = termos_relevantes("para que como com uma dos reator reator")
    assert "para" not in termos and "como" not in termos
    assert "reator" in termos


def test_texto_vazio_nao_gera_termos():
    assert termos_relevantes("") == []
