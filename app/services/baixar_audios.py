import os
import shutil
import subprocess
import zipfile
from datetime import datetime
from time import sleep

from dotenv import load_dotenv
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import Select, WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager
import requests

load_dotenv()

FORMATO_DATA_HORA_REGISTRO = "%d/%m/%Y %H:%M:%S"

DOWNLOADS_ROOT = os.path.abspath("downloads")
DADOS_DIR = os.path.join(DOWNLOADS_ROOT, "dados")
PREFIXO_ZIP = "audios_parte"
TAMANHO_MAXIMO_ZIP_BYTES = 50 * 1024 * 1024


def _criar_navegador():
    options = webdriver.ChromeOptions()
    options.add_argument("--headless=new")
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

    os.makedirs(DADOS_DIR, exist_ok=True)

    prefs = {
        "download.default_directory": DADOS_DIR,
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
    return navegador


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
    try:
        select_el = WebDriverWait(navegador, 10).until(
            EC.presence_of_element_located((By.ID, "limitoption"))
        )
    except Exception:
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

    if ref_antiga is not None:
        WebDriverWait(navegador, 20).until(EC.staleness_of(ref_antiga))

    WebDriverWait(navegador, 20).until(
        EC.presence_of_all_elements_located(
            (By.XPATH, "//a[normalize-space(text())='Ouvir']")
        )
    )


def _pagina_atual_valor(navegador):
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
    "Ouvir", o href de download e a data/hora do registro. A paginação é
    dinâmica (a janela de números muda conforme se avança), por isso a cada
    passo clicamos sempre no menor número de página ainda não visitado.
    """
    hrefs_vistos = set()
    todos_dados = []
    paginas_visitadas = set()
    max_iteracoes = 500

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

        yield (
            f"Página {pagina_atual or '?'}: {len(links)} links "
            f"({novos_nesta_pagina} novos)"
        )

        if novos_nesta_pagina == 0:
            break

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
            break

        _, proximo_li = candidatos[0]
        ref_antiga = links[0]
        _clicar_com_seguranca(navegador, proximo_li.find_element(By.TAG_NAME, "a"))

        try:
            WebDriverWait(navegador, 10).until(EC.staleness_of(ref_antiga))
        except Exception:
            break

    yield f"Total: {len(todos_dados)} links coletados"
    return todos_dados


def _nome_arquivo_por_data(href, data_hora_texto):
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
    if data_hora_texto:
        try:
            dt = datetime.strptime(data_hora_texto, FORMATO_DATA_HORA_REGISTRO)
            return dt.strftime("%d_%m")
        except ValueError:
            pass

    return "sem_data"


def _baixar_com_sessao(navegador, dados):
    """
    Baixa cada arquivo em uma subpasta de DADOS_DIR nomeada pelo dia da
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

        pasta_dia = os.path.join(DADOS_DIR, _pasta_dia_por_data(data_hora_texto))
        os.makedirs(pasta_dia, exist_ok=True)
        destino = os.path.join(pasta_dia, nome_arquivo)

        # evita sobrescrever caso duas gravações caiam no mesmo minuto
        raiz, extensao = os.path.splitext(nome_arquivo)
        contador = 1
        while os.path.exists(destino):
            destino = os.path.join(pasta_dia, f"{raiz}_{contador}{extensao}")
            contador += 1

        yield f"Baixando {i}/{len(dados)}: {os.path.relpath(destino, DADOS_DIR)}"
        r = sessao.get(href, stream=True, timeout=120)
        r.raise_for_status()
        with open(destino, "wb") as f:
            for chunk in r.iter_content(8192):
                f.write(chunk)

    yield "Downloads concluídos!"


def _converter_3gp_para_mp3():
    """
    Converte, via ffmpeg, todos os .3gp de DADOS_DIR (incluindo subpastas
    por dia) para .mp3, aplicando filtros para reduzir ruído de ambientes
    barulhentos mantendo a voz clara. Após uma conversão bem-sucedida, o
    .3gp original é removido.
    """
    arquivos_3gp = []
    for raiz, _, arquivos in os.walk(DADOS_DIR):
        for nome in arquivos:
            if nome.lower().endswith(".3gp"):
                arquivos_3gp.append(os.path.join(raiz, nome))
    arquivos_3gp.sort()

    if not arquivos_3gp:
        yield "Nenhum arquivo .3gp encontrado para converter."
        return

    yield f"Convertendo {len(arquivos_3gp)} arquivo(s) .3gp para .mp3..."

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
        nome = os.path.relpath(origem, DADOS_DIR)

        yield f"Convertendo {i}/{len(arquivos_3gp)}: {nome}"
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
            yield f"  ❌ Falha ao converter {nome}: {erro}"
            continue

        os.remove(origem)

    yield "Conversão concluída!"


def _zipar_audios():
    """
    Compacta os áudios de DADOS_DIR em várias partes .zip (audios_parteNN.zip),
    cada uma com no máximo TAMANHO_MAXIMO_ZIP_BYTES, para que o download no
    navegador não precise de um único arquivo pesado. Retorna a lista de
    caminhos dos zips gerados, em ordem.
    """
    arquivos = []
    for raiz, _, nomes in os.walk(DADOS_DIR):
        for nome in nomes:
            caminho = os.path.join(raiz, nome)
            arquivos.append((caminho, os.path.getsize(caminho)))
    arquivos.sort()

    caminhos_zip = []
    if not arquivos:
        return caminhos_zip

    contador_partes = 1

    def _abrir_nova_parte():
        nonlocal contador_partes
        caminho = os.path.join(
            DOWNLOADS_ROOT, f"{PREFIXO_ZIP}{contador_partes:02d}.zip"
        )
        contador_partes += 1
        caminhos_zip.append(caminho)
        return zipfile.ZipFile(caminho, "w", zipfile.ZIP_DEFLATED)

    zip_atual = _abrir_nova_parte()
    tamanho_atual = 0
    try:
        for caminho, tamanho in arquivos:
            if tamanho_atual > 0 and tamanho_atual + tamanho > TAMANHO_MAXIMO_ZIP_BYTES:
                zip_atual.close()
                zip_atual = _abrir_nova_parte()
                tamanho_atual = 0
            arcname = os.path.relpath(caminho, DADOS_DIR)
            zip_atual.write(caminho, arcname)
            tamanho_atual += tamanho
    finally:
        zip_atual.close()

    return caminhos_zip


def listar_zips_disponiveis():
    """
    Lista os zips de áudio disponíveis em DOWNLOADS_ROOT (gerados na última
    execução de baixar_audios), com nome e tamanho em bytes, ordenados.
    """
    if not os.path.isdir(DOWNLOADS_ROOT):
        return []

    resultado = []
    for nome in sorted(os.listdir(DOWNLOADS_ROOT)):
        if nome.startswith(PREFIXO_ZIP) and nome.endswith(".zip"):
            caminho = os.path.join(DOWNLOADS_ROOT, nome)
            if os.path.isfile(caminho):
                resultado.append({"nome": nome, "tamanho_bytes": os.path.getsize(caminho)})
    return resultado


def caminho_zip_seguro(nome_arquivo):
    """
    Resolve o caminho absoluto de um zip de áudio a partir do nome informado
    pelo cliente, validando o formato esperado para evitar path traversal.
    Retorna None se o nome for inválido ou o arquivo não existir.
    """
    nome = os.path.basename(nome_arquivo or "")
    if not (nome.startswith(PREFIXO_ZIP) and nome.endswith(".zip")):
        return None

    caminho = os.path.join(DOWNLOADS_ROOT, nome)
    if not os.path.isfile(caminho):
        return None

    return caminho


def _esvaziar_pasta_downloads():
    """
    Remove todo o conteúdo de DOWNLOADS_ROOT, mantendo a pasta em si.
    """
    if os.path.isdir(DOWNLOADS_ROOT):
        for nome in os.listdir(DOWNLOADS_ROOT):
            caminho = os.path.join(DOWNLOADS_ROOT, nome)
            if os.path.isdir(caminho):
                shutil.rmtree(caminho, ignore_errors=True)
            else:
                try:
                    os.remove(caminho)
                except OSError:
                    pass
    os.makedirs(DOWNLOADS_ROOT, exist_ok=True)


def baixar_audios():
    """
    Gerador que faz login no BE, coleta e baixa todas as gravações
    disponíveis, converte para mp3 e compacta tudo em audios.zip, emitindo
    mensagens de progresso a cada etapa (para acompanhamento via SSE).
    """
    navegador = None
    try:
        yield "Esvaziando pasta de downloads..."
        _esvaziar_pasta_downloads()

        yield "Iniciando download de áudios..."

        navegador = _criar_navegador()
        WebDriverWait(navegador, 20)

        user = os.getenv("USER_BE")
        password = os.getenv("PASSWORD_BE")
        url = os.getenv("URL_BE")

        yield "Acessando o sistema BE..."
        navegador.get(url)

        navegador.find_element(By.ID, "username").send_keys(user)
        sleep(0.5)
        navegador.find_element(By.ID, "password").send_keys(password)

        navegador.find_element(
            By.CSS_SELECTOR,
            "#login-page > div > form > div:nth-child(5) > div > button",
        ).click()

        yield "Login realizado com sucesso!"
        sleep(5)

        navegador.find_element(
            By.CSS_SELECTOR, "#slide-out > li:nth-child(2) > a"
        ).click()
        yield "Clicou no link de gravação"
        sleep(2)

        navegador.find_element(
            By.CSS_SELECTOR, "#load_div > div:nth-child(11) > a"
        ).click()
        yield "Acessou a página de gravação"

        _definir_registros_por_pagina(navegador, "50")
        yield "Registros por página ajustados para 50"

        dados = yield from _coletar_dados_todas_paginas(navegador)

        yield from _baixar_com_sessao(navegador, dados)

        yield from _converter_3gp_para_mp3()

        caminhos_zip = _zipar_audios()
        if caminhos_zip:
            yield f"Áudios compactados em {len(caminhos_zip)} arquivo(s) zip (máx. 50 MB cada):"
            for caminho in caminhos_zip:
                tamanho_mb = os.path.getsize(caminho) / (1024 * 1024)
                yield f"  - {os.path.basename(caminho)} ({tamanho_mb:.1f} MB)"
        else:
            yield "Nenhum áudio encontrado para compactar."

        yield "Download de áudios concluído com sucesso!"

    except Exception as e:
        yield f"❌ ERRO: {str(e)}"
    finally:
        if navegador:
            try:
                navegador.quit()
            except Exception:
                pass
