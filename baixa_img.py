import os
import shutil
import subprocess
from datetime import datetime, timedelta
from time import sleep
from urllib.parse import urljoin

from dotenv import load_dotenv
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import Select, WebDriverWait
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.support import expected_conditions as EC
import requests


load_dotenv()

FORMATO_DATA_HORA_REGISTRO = "%d/%m/%Y %H:%M:%S"
PASTA_IMAGENS_JPG = "jpg"
XPATH_SPAN_JPG = (
    "//span[contains(@class,'name-meta') and normalize-space()='jpg']"
)


def _criar_navegador():
    options = webdriver.ChromeOptions()
    # options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--disable-software-rasterizer")
    options.add_argument("--disable-extensions")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument("--window-size=1920,1080")
    options.add_argument("--no-proxy-server")
    options.add_argument("--proxy-server='direct://'")
    options.add_argument("--proxy-bypass-list=*")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)

    download_dir = os.path.abspath("downloads")
    os.makedirs(download_dir, exist_ok=True)

    prefs = {
        "download.default_directory": download_dir,
        "download.prompt_for_download": False,
        "download.directory_upgrade": True,
        "plugins.always_open_pdf_externally": True,
        "profile.default_content_setting_values.notifications": 2,
        "profile.default_content_settings.popups": 0,
    }
    options.add_experimental_option("prefs", prefs)

    chrome_bin = os.getenv("CHROME_BIN")
    chromedriver_path = os.getenv("CHROMEDRIVER_PATH")

    if chrome_bin and chromedriver_path:
        options.binary_location = chrome_bin
        servico = Service(chromedriver_path)
    else:
        servico = Service(ChromeDriverManager().install())

    navegador = webdriver.Chrome(service=servico, options=options)
    navegador.implicitly_wait(10)
    return navegador, download_dir


def _parse_css_px(style, prop, default=0):
    """Extrai valor numérico em px de uma propriedade CSS inline."""
    if not style:
        return default
    for parte in style.split(";"):
        parte = parte.strip()
        if parte.startswith(f"{prop}:"):
            valor = parte.split(":", 1)[1].strip().replace("px", "").strip()
            try:
                return float(valor)
            except ValueError:
                return default
    return default


def _rail_scroll_lista_visivel(navegador):
    """Retorna a barra vertical Perfect Scrollbar visível da lista de contatos."""
    rails = navegador.find_elements(By.CSS_SELECTOR, "div.ps__rail-y")
    for rail in rails:
        if rail.is_displayed():
            return rail
    return rails[0] if rails else None


def _elemento_scroll_lista_contatos(navegador, rail=None):
    """Retorna o elemento com scroll associado ao ps__rail-y."""
    if rail is None:
        rail = _rail_scroll_lista_visivel(navegador)
    if rail is None:
        return None
    return navegador.execute_script(
        """
        const rail = arguments[0];
        const ps = rail.closest('.ps');
        if (!ps) return null;
        return ps.querySelector('.ps__element') || ps;
        """,
        rail,
    )


def _posicao_scroll_lista_contatos(navegador, rail=None):
    el = _elemento_scroll_lista_contatos(navegador, rail)
    if el is None:
        return 0
    return navegador.execute_script("return arguments[0].scrollTop;", el) or 0


def _scroll_lista_no_fim(navegador, rail, thumb):
    """True quando o thumb está no fim do trilho (não dá para rolar mais)."""
    thumb_top = _parse_css_px(thumb.get_attribute("style") or "", "top", 0)
    thumb_height = thumb.size.get("height") or 0
    rail_height = rail.size.get("height") or 0
    return thumb_top + thumb_height >= rail_height - 2


def _rolar_lista_contatos_para_baixo(navegador):
    """
    Rola a lista de contatos para baixo clicando na ps__rail-y abaixo do
    ps__thumb-y (Perfect Scrollbar). Retorna False se não houver mais scroll.
    """
    rail = _rail_scroll_lista_visivel(navegador)
    if rail is None:
        raise RuntimeError("Barra de scroll (div.ps__rail-y) não encontrada")

    thumb = rail.find_element(By.CSS_SELECTOR, "div.ps__thumb-y")
    if _scroll_lista_no_fim(navegador, rail, thumb):
        return False

    scroll_antes = _posicao_scroll_lista_contatos(navegador, rail)
    thumb_top = _parse_css_px(thumb.get_attribute("style") or "", "top", 0)
    thumb_height = thumb.size.get("height") or 36
    rail_height = rail.size.get("height") or 400
    rail_width = max(rail.size.get("width") or 8, 2)

    restante = rail_height - (thumb_top + thumb_height)
    offset_y = int(
        min(rail_height - 3, thumb_top + thumb_height + max(40, restante * 0.45))
    )

    ActionChains(navegador).move_to_element_with_offset(
        rail, rail_width // 2, offset_y
    ).click().perform()
    sleep(0.7)

    scroll_depois = _posicao_scroll_lista_contatos(navegador, rail)
    if scroll_depois > scroll_antes + 1:
        return True

    if _scroll_lista_no_fim(navegador, rail, thumb):
        return False

    # fallback: incrementa scrollTop diretamente no container
    el = _elemento_scroll_lista_contatos(navegador, rail)
    if el is not None:
        navegador.execute_script(
            """
            const el = arguments[0];
            const delta = Math.max(120, Math.floor(el.clientHeight * 0.6));
            el.scrollTop = Math.min(el.scrollHeight, el.scrollTop + delta);
            """,
            el,
        )
        sleep(0.7)
        return _posicao_scroll_lista_contatos(navegador, rail) > scroll_antes + 1

    return False


def _pesquisar_contato(navegador, termo="jpg"):
    """
    Digita o termo em #searchString e clica em #searchbutton antes de
    procurar o contato na lista.
    """
    campo = WebDriverWait(navegador, 20).until(
        EC.presence_of_element_located((By.ID, "searchString"))
    )
    campo.clear()
    campo.send_keys(termo)
    print(f"Digitou '{termo}' em #searchString")
    sleep(0.3)

    botao = WebDriverWait(navegador, 10).until(
        EC.element_to_be_clickable((By.ID, "searchbutton"))
    )
    try:
        botao.click()
    except Exception:
        navegador.execute_script(
            "document.querySelector('#searchbutton').click();"
        )
    print("Clicou em #searchbutton")
    sleep(1.5)


def _span_jpg_clicavel(navegador):
    """Retorna o span 'jpg' se existir e estiver clicável na viewport."""
    for el in navegador.find_elements(By.XPATH, XPATH_SPAN_JPG):
        if not el.is_displayed():
            continue
        try:
            WebDriverWait(navegador, 1).until(EC.element_to_be_clickable(el))
            return el
        except Exception:
            continue
    return None


def _encontrar_e_clicar_jpg(navegador, tentativas_max=80):
    """
    Busca o contato 'jpg' na lista. Se não estiver visível, rola para baixo
    via ps__rail-y até encontrar ou esgotar o scroll.
    """
    _pesquisar_contato(navegador, "jpg")

    for tentativa in range(1, tentativas_max + 1):
        span_jpg = _span_jpg_clicavel(navegador)
        if span_jpg is not None:
            _clicar_com_seguranca(navegador, span_jpg)
            print("Clicou em jpg")
            return

        print(
            f"Elemento 'jpg' não visível (tentativa {tentativa}), "
            "rolando lista para baixo..."
        )
        if not _rolar_lista_contatos_para_baixo(navegador):
            break

    raise RuntimeError(
        "Elemento 'jpg' não encontrado após rolar a lista até o fim"
    )


def _clicar_com_seguranca(navegador, elemento):
    """
    Clica em um elemento evitando o erro "element click intercepted"
    causado por menus fixos (sticky) sobrepondo o ponto de clique.
    """
    navegador.execute_script(
        "arguments[0].scrollIntoView({block: 'center'});", elemento
    )
    sleep(0.3)
    try:
        elemento.click()
    except Exception:
        navegador.execute_script("arguments[0].click();", elemento)


def _definir_registros_por_pagina(navegador, valor="50"):
    """
    Ajusta o select #limitoption para reduzir o número de páginas a percorrer.
    """
    try:
        select_el = WebDriverWait(navegador, 10).until(
            EC.presence_of_element_located((By.ID, "limitoption"))
        )
    except Exception:
        print("Select #limitoption não encontrado, mantendo paginação padrão.")
        return

    select = Select(select_el)
    if select.first_selected_option.get_attribute("value") == valor:
        return

    try:
        ref_antiga = navegador.find_element(
            By.XPATH, "//a[normalize-space(text())='Ouvir']"
        )
    except Exception:
        ref_antiga = None

    select.select_by_value(valor)
    print(f"Registros por página ajustado para {valor}")

    if ref_antiga is not None:
        WebDriverWait(navegador, 20).until(EC.staleness_of(ref_antiga))

    WebDriverWait(navegador, 20).until(
        EC.presence_of_all_elements_located(
            (By.XPATH, "//a[normalize-space(text())='Ouvir']")
        )
    )


def _pagina_atual_valor(navegador):
    """
    Retorna o "value" da página atualmente ativa em #pagingid, se houver
    algum marcador reconhecível (ex.: li.livepage).
    """
    for classe in ("livepage", "active", "current", "selected"):
        try:
            li = navegador.find_element(By.CSS_SELECTOR, f"#pagingid > li.{classe}")
            return li.get_attribute("value")
        except Exception:
            continue
    return None


def _coletar_dados_todas_paginas(navegador):
    """
    Percorre todas as páginas de resultados coletando, para cada link
    "Ouvir", o href de download e a data/hora do registro (texto do <td>
    imediatamente anterior ao <td> que contém o link).

    A paginação é dinâmica: o componente só exibe uma janela de números por
    vez (ex.: 1, 2, 3, "Último"). O item "Último" carrega um "value" próprio
    e, ao ser clicado, pula direto para aquela página em vez de avançar uma
    página por vez — o que faz a janela ser recalculada revelando novos
    números (ex.: 4 a 7) e pulando páginas intermediárias que ainda não
    foram visitadas.

    Por isso, a cada passo, olhamos os números de página atualmente visíveis
    em #pagingid, ignoramos os já visitados, e clicamos sempre no menor
    valor ainda não visitado — garantindo que nenhuma página seja pulada.
    """
    hrefs_vistos = set()
    todos_dados = []
    paginas_visitadas = set()
    max_iteracoes = 500  # trava de segurança contra loop infinito

    for _ in range(max_iteracoes):
        WebDriverWait(navegador, 20).until(
            EC.presence_of_all_elements_located(
                (By.XPATH, "//a[normalize-space(text())='Ouvir']")
            )
        )

        links = navegador.find_elements(
            By.XPATH, "//a[normalize-space(text())='Ouvir']"
        )

        novos_nesta_pagina = 0
        for link in links:
            href = link.get_attribute("href").strip()
            if href in hrefs_vistos:
                continue
            hrefs_vistos.add(href)

            try:
                td_data = link.find_element(
                    By.XPATH, "./ancestor::td[1]/preceding-sibling::td[1]"
                )
                data_hora = td_data.text.strip()
            except Exception:
                data_hora = None

            todos_dados.append({"href": href, "data_hora": data_hora})
            novos_nesta_pagina += 1

        pagina_atual = _pagina_atual_valor(navegador)
        if pagina_atual:
            paginas_visitadas.add(pagina_atual)

        print(
            f"Página {pagina_atual or '?'}: {len(links)} links "
            f"({novos_nesta_pagina} novos)"
        )

        if novos_nesta_pagina == 0:
            break  # nada de novo: já cobrimos todos os registros

        itens = navegador.find_elements(By.CSS_SELECTOR, "#pagingid > li[value]")
        candidatos = sorted(
            (
                (int(it.get_attribute("value")), it)
                for it in itens
                if it.get_attribute("value") not in paginas_visitadas
            ),
            key=lambda item: item[0],
        )

        if not candidatos:
            break  # nenhuma página nova visível na paginação

        _, proximo_li = candidatos[0]
        ref_antiga = links[0]
        _clicar_com_seguranca(navegador, proximo_li.find_element(By.TAG_NAME, "a"))

        try:
            WebDriverWait(navegador, 10).until(EC.staleness_of(ref_antiga))
        except Exception:
            break  # o clique não avançou a página: fim da listagem

    print(f"Total: {len(todos_dados)} links coletados")
    return todos_dados


def _nome_arquivo_por_data(href, data_hora_texto):
    """
    Gera o nome do arquivo a partir da data/hora do registro, no formato
    dia_mes_hora_min, preservando a extensão original do arquivo.
    Se a data/hora não puder ser interpretada, mantém o nome original.
    """
    filename_original = href.split("filename=")[1].split("&")[0].strip()
    _, extensao = os.path.splitext(filename_original)

    if data_hora_texto:
        try:
            dt = datetime.strptime(data_hora_texto, FORMATO_DATA_HORA_REGISTRO)
            return f"{dt.strftime('%d_%m_%H_%M')}{extensao}"
        except ValueError:
            pass

    return filename_original


def _pasta_dia_por_data(data_hora_texto):
    """
    Retorna o nome da pasta do dia (formato dia_mes) a partir da data/hora
    do registro. Se não for possível interpretar, agrupa em "sem_data".
    """
    if data_hora_texto:
        try:
            dt = datetime.strptime(data_hora_texto, FORMATO_DATA_HORA_REGISTRO)
            return dt.strftime("%d_%m")
        except ValueError:
            pass

    return "sem_data"


def _baixar_com_sessao(navegador, download_dir, dados):
    """
    Baixa cada arquivo em uma subpasta de download_dir nomeada pelo dia da
    gravação (formato dia_mes), renomeando o arquivo pela data/hora.
    """
    sessao = requests.Session()
    for c in navegador.get_cookies():
        sessao.cookies.set(c["name"], c["value"])
    sessao.headers.update(
        {"User-Agent": navegador.execute_script("return navigator.userAgent")}
    )

    for i, item in enumerate(dados, 1):
        href = item["href"]
        data_hora_texto = item.get("data_hora")
        nome_arquivo = _nome_arquivo_por_data(href, data_hora_texto)

        pasta_dia = os.path.join(download_dir, _pasta_dia_por_data(data_hora_texto))
        os.makedirs(pasta_dia, exist_ok=True)
        destino = os.path.join(pasta_dia, nome_arquivo)

        # evita sobrescrever caso duas gravações caiam no mesmo minuto
        raiz, extensao = os.path.splitext(nome_arquivo)
        contador = 1
        while os.path.exists(destino):
            destino = os.path.join(pasta_dia, f"{raiz}_{contador}{extensao}")
            contador += 1

        print(f"Baixando {i}/{len(dados)}: {os.path.relpath(destino, download_dir)}")
        r = sessao.get(href, stream=True, timeout=120)
        r.raise_for_status()
        with open(destino, "wb") as f:
            for chunk in r.iter_content(8192):
                f.write(chunk)

        # Colocar break para testar apenas o primeiro download
        break
    print("Downloads concluídos!")


def _converter_3gp_para_mp3(download_dir):
    """
    Converte, via ffmpeg, todos os arquivos .3gp de download_dir (incluindo
    subpastas por dia) para .mp3. Após uma conversão bem-sucedida, o .3gp
    original é removido.
    """
    arquivos_3gp = []
    for raiz, _, arquivos in os.walk(download_dir):
        for nome in arquivos:
            if nome.lower().endswith(".3gp"):
                arquivos_3gp.append(os.path.join(raiz, nome))
    arquivos_3gp.sort()

    if not arquivos_3gp:
        print("Nenhum arquivo .3gp encontrado para converter.")
        return

    print(f"Convertendo {len(arquivos_3gp)} arquivo(s) .3gp para .mp3...")

    filtro_audio = (
        "highpass=f=100,"
        "lowpass=f=7000,"
        "afftdn=nf=-27:tn=1,"
        "dynaudnorm=f=500:g=31,"
        "treble=g=4:f=3000,"
        "alimiter=limit=0.95"
    )

    for i, origem in enumerate(arquivos_3gp, 1):
        destino = os.path.splitext(origem)[0] + ".mp3"
        nome = os.path.relpath(origem, download_dir)

        print(f"Convertendo {i}/{len(arquivos_3gp)}: {nome}")
        resultado = subprocess.run(
            [
                "ffmpeg", "-y",
                "-i", origem,
                "-af", filtro_audio,
                "-ar", "44100",
                "-c:a", "libmp3lame",
                "-qscale:a", "0",
                destino,
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        if resultado.returncode != 0:
            erro = resultado.stderr.decode(errors="ignore")
            print(f"  ❌ Falha ao converter {nome}: {erro}")
            continue

        os.remove(origem)

    print("Conversão concluída!")


def _zipar_audios(download_dir, nome_zip="audios"):
    """
    Compacta as pastas por dia dentro de download_dir em um único arquivo
    <nome_zip>.zip, salvo no diretório pai de download_dir.
    """
    destino_base = os.path.join(os.path.dirname(download_dir), nome_zip)
    caminho_zip = shutil.make_archive(destino_base, "zip", download_dir)
    print(f"Áudios compactados em: {caminho_zip}")
    return caminho_zip


def _parse_data_hora_whatsapp(texto):
    """Interpreta textos como '16/8/2026 12:2:45' (sem zero à esquerda)."""
    if not texto:
        return None
    texto = texto.strip()
    for fmt in (FORMATO_DATA_HORA_REGISTRO, "%d/%m/%Y %H:%M"):
        try:
            return datetime.strptime(texto, fmt)
        except ValueError:
            continue
    return None


def _destino_imagem_whatsapp(download_dir, data_hora_texto, extensao=".jpg"):
    """
    Monta o caminho final da imagem: downloads/jpg/<dia_mes>/<dd_mm_HH_MM_SS>.jpg
    """
    dt = _parse_data_hora_whatsapp(data_hora_texto)
    pasta_base = os.path.join(download_dir, PASTA_IMAGENS_JPG)

    if dt is None:
        pasta_dia = os.path.join(pasta_base, "sem_data")
        nome = f"sem_data{extensao}"
    else:
        pasta_dia = os.path.join(pasta_base, dt.strftime("%d_%m"))
        nome = f"{dt.strftime('%d_%m_%H_%M_%S')}{extensao}"

    os.makedirs(pasta_dia, exist_ok=True)
    return os.path.join(pasta_dia, nome)


def _caminho_sem_sobrescrever(caminho):
    """Se o arquivo já existir, gera 'nome (1).ext', 'nome (2).ext', etc."""
    if not os.path.exists(caminho):
        return caminho

    pasta, nome = os.path.split(caminho)
    raiz, extensao = os.path.splitext(nome)
    contador = 1
    while True:
        candidato = os.path.join(pasta, f"{raiz} ({contador}){extensao}")
        if not os.path.exists(candidato):
            return candidato
        contador += 1


def _extensao_do_href(href, fallback=".jpg"):
    try:
        filename = href.split("filename=")[1].split("&")[0].strip()
        _, extensao = os.path.splitext(filename)
        return extensao or fallback
    except (IndexError, AttributeError):
        return fallback


def _criar_sessao_requests(navegador):
    sessao = requests.Session()
    for c in navegador.get_cookies():
        sessao.cookies.set(c["name"], c["value"])
    sessao.headers.update(
        {"User-Agent": navegador.execute_script("return navigator.userAgent")}
    )
    return sessao


def _arquivo_baixado_ok(caminho):
    return os.path.isfile(caminho) and os.path.getsize(caminho) > 0


def _baixar_arquivo(sessao, href_abs, destino):
    r = sessao.get(href_abs, stream=True, timeout=120)
    r.raise_for_status()
    with open(destino, "wb") as f:
        for chunk in r.iter_content(8192):
            f.write(chunk)
    if not _arquivo_baixado_ok(destino):
        if os.path.exists(destino):
            os.remove(destino)
        raise RuntimeError(f"Arquivo inválido após download: {destino}")


def _apagar_mensagem_conversa(navegador, mensagem, val):
    """Apaga a mensagem na UI só depois do download confirmado."""
    try:
        botao = mensagem.find_element(By.CSS_SELECTOR, "span.check.pull-right")
        _clicar_com_seguranca(navegador, botao)
    except Exception:
        navegador.execute_script("deleteSingleRec(arguments[0]);", val)

    try:
        WebDriverWait(navegador, 10).until(EC.staleness_of(mensagem))
    except Exception:
        sleep(1)


def _elemento_scroll_conversa(navegador):
    """Retorna o ancestral com scroll da conversa (ou o próprio container)."""
    container = navegador.find_element(
        By.CSS_SELECTOR, "div.conversation-container"
    )
    return navegador.execute_script(
        """
        let el = arguments[0];
        while (el) {
            const style = window.getComputedStyle(el);
            const oy = style.overflowY;
            if ((oy === 'auto' || oy === 'scroll' || oy === 'overlay')
                && el.scrollHeight > el.clientHeight + 5) {
                return el;
            }
            el = el.parentElement;
        }
        return arguments[0];
        """,
        container,
    )


def _vals_mensagens_visiveis(navegador):
    # received = recebidas; sent = enviadas (ex.: IMG-...jpg do próprio aparelho)
    mensagens = navegador.find_elements(
        By.CSS_SELECTOR,
        "div.conversation-container div.message.media",
    )
    vals = []
    for m in mensagens:
        val = m.get_attribute("val")
        if val:
            vals.append(val)
    return mensagens, vals


def _rolar_conversa_para_baixo(navegador):
    el = _elemento_scroll_conversa(navegador)
    navegador.execute_script(
        "arguments[0].scrollTop = arguments[0].scrollHeight;", el
    )
    sleep(0.8)


def _rolar_conversa_para_cima(navegador, fracao=0.85):
    """
    Sobe o scroll para carregar mensagens mais antigas (lazy load).
    Retorna (scrollTop_antes, scrollTop_depois).
    """
    el = _elemento_scroll_conversa(navegador)
    antes = navegador.execute_script("return arguments[0].scrollTop;", el)
    navegador.execute_script(
        """
        const el = arguments[0];
        const delta = Math.max(120, Math.floor(el.clientHeight * arguments[1]));
        el.scrollTop = Math.max(0, el.scrollTop - delta);
        """,
        el,
        fracao,
    )
    sleep(1.2)
    depois = navegador.execute_script("return arguments[0].scrollTop;", el)
    return antes, depois


def _carregar_mais_antigas(navegador, tentativas_max=4):
    """
    Rola para cima até aparecerem mensagens novas ou esgotar tentativas.
    Retorna True se novas mensagens entraram no DOM.
    """
    _, vals_antes = _vals_mensagens_visiveis(navegador)
    set_antes = set(vals_antes)

    for i in range(tentativas_max):
        scroll_antes, scroll_depois = _rolar_conversa_para_cima(navegador)
        _, vals_depois = _vals_mensagens_visiveis(navegador)
        novas = set(vals_depois) - set_antes
        if novas:
            print(
                f"Scroll ↑ carregou {len(novas)} mensagem(ns) antiga(s) "
                f"(tentativa {i + 1})"
            )
            return True
        if scroll_depois <= 0 and scroll_antes <= 0:
            break
        if scroll_depois == scroll_antes and scroll_depois <= 0:
            break

    return False


def _baixar_e_apagar_imagens_jpg(navegador, download_dir):
    """
    Percorre as mensagens de mídia do mais recente (embaixo) para o mais
    antigo (em cima). O histórico carrega sob demanda: periodicamente rola
    para cima para trazer imagens mais antigas.
    Sempre baixa de novo: se o arquivo já existir, salva como (1), (2), etc.
    Só apaga na UI após confirmar o arquivo em disco.
    """
    WebDriverWait(navegador, 20).until(
        EC.presence_of_element_located(
            (By.CSS_SELECTOR, "div.conversation-container")
        )
    )
    sleep(2)

    _rolar_conversa_para_baixo(navegador)

    sessao = _criar_sessao_requests(navegador)
    vals_processados = set()
    baixados = 0
    falhas = 0
    processados_desde_scroll = 0
    SCROLL_A_CADA = 5

    while True:
        mensagens, _ = _vals_mensagens_visiveis(navegador)

        # mais abaixo = mais recente → processa do fim para o início
        mensagem = None
        for candidata in reversed(mensagens):
            val = candidata.get_attribute("val")
            if val and val not in vals_processados:
                mensagem = candidata
                break

        if mensagem is None:
            if _carregar_mais_antigas(navegador):
                processados_desde_scroll = 0
                continue
            break

        val = mensagem.get_attribute("val")

        try:
            data_hora_texto = mensagem.find_element(
                By.CSS_SELECTOR, "span.time"
            ).text.strip()
            link = mensagem.find_element(By.CSS_SELECTOR, "a.media-ui-btn")
            href = link.get_attribute("href")
            if not href:
                raise RuntimeError("Mensagem sem href de download")

            href_abs = urljoin(navegador.current_url, href)
            extensao = _extensao_do_href(href_abs)
            destino = _caminho_sem_sobrescrever(
                _destino_imagem_whatsapp(
                    download_dir, data_hora_texto, extensao
                )
            )
            relativo = os.path.relpath(destino, download_dir)

            print(f"Baixando: {relativo} ({data_hora_texto})")
            _baixar_arquivo(sessao, href_abs, destino)

            if not _arquivo_baixado_ok(destino):
                raise RuntimeError("Download não confirmado em disco")

            print(f"Download OK, apagando mensagem {val}...")
            _apagar_mensagem_conversa(navegador, mensagem, val)
            vals_processados.add(val)
            baixados += 1

            processados_desde_scroll += 1
            if processados_desde_scroll >= SCROLL_A_CADA:
                print("Scroll periódico ↑ para carregar imagens mais antigas...")
                _carregar_mais_antigas(navegador, tentativas_max=2)
                processados_desde_scroll = 0

        except Exception as e:
            falhas += 1
            vals_processados.add(val)
            print(f"❌ Falha na mensagem {val}: {e}")
            continue

    print(
        f"Conversa jpg: {baixados} baixado(s), {falhas} falha(s)."
    )
    return baixados, falhas


def baixar_imagens():
    navegador = None
    download_dir = None
    try:
        print("Iniciando download de imagens...")
        navegador, download_dir = _criar_navegador()
        WebDriverWait(navegador, 20)

        user = os.getenv("USER_BE")
        password = os.getenv("PASSWORD_BE")
        url = os.getenv("URL_BE")

        print("Acessando o sistema BE...")
        navegador.get(url)

        navegador.find_element(By.ID, "username").send_keys(user)
        sleep(0.5)

        navegador.find_element(By.ID, "password").send_keys(password)

        navegador.find_element(
            By.CSS_SELECTOR,
            "#login-page > div > form > div:nth-child(5) > div > button",
        ).click()

        print("Login realizado com sucesso!")
        sleep(5)

        navegador.find_element(
            By.CSS_SELECTOR, "#slide-out > li:nth-child(2) > a"
        ).click()
        print("Clicou no painel antigo")
        sleep(2)

        navegador.find_element(
            By.CSS_SELECTOR, "#load_div > div:nth-child(4) > a"
        ).click()
        print("Acessou a página de whatsapp")

        _encontrar_e_clicar_jpg(navegador)

        return _baixar_e_apagar_imagens_jpg(navegador, download_dir)

    except Exception as e:
        print(f"❌ ERRO: {str(e)}")
        return None
    finally:
        if navegador:
            try:
                navegador.quit()
            except Exception:
                pass


if __name__ == "__main__":
    rodada = 0
    while True:
        rodada += 1
        print(f"\n===== Rodada {rodada} =====")
        resultado = baixar_imagens()

        if resultado is None:
            print("Execução com erro — reiniciando o script...")
            sleep(3)
            continue

        baixados, falhas = resultado
        if baixados == 0 and falhas == 0:
            print("Nada a baixar e sem falhas. Encerrando.")
            break

        print(
            f"Resultado {baixados} baixado(s), {falhas} falha(s) "
            "— reiniciando o script..."
        )
        sleep(3)
