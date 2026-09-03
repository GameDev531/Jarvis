# Suítes

Três pastas, e a diferença entre elas é **de que o teste depende**, não de
tamanho nem de estilo.

    tests/unit/          nada além do processo. Sem rede, sem DNS, sem
                         Chromium, sem Home Assistant, sem disco além de
                         `tmp_path`. Rodam sempre, em qualquer máquina.
    tests/integration/   dependem de algo de fora: rede, navegador de verdade,
                         um serviço rodando. Todo arquivo aqui carrega um
                         marcador (`network`, `browser`, `integration`).
    tests/e2e/           o James inteiro, de ponta a ponta.

A raiz de `tests/` guarda a suíte histórica, que também é unitária. Arquivo
novo nasce na pasta certa.

## Por que isso existe

Um teste de auditoria resolvia `example.com` de verdade. Numa máquina sem DNS
ele falhava — com o código correto. Um teste que reprova por causa da rede
ensina a ignorar teste vermelho, que é o pior hábito que uma suíte pode criar.

## Como rodar

    # o que a CI unitária roda: sem internet, sem Chromium
    pytest -m "not network and not browser and not integration and not e2e"

    # tudo, numa máquina preparada
    python -m playwright install chromium
    pytest

Os marcadores estão declarados em `pyproject.toml` com `--strict-markers`:
marcador com nome errado é erro, não é silêncio.
