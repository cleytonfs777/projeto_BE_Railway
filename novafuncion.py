import os
import shutil
import subprocess
from datetime import datetime, timedelta
from time import sleep

from dotenv import load_dotenv
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import Select, WebDriverWait
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.support import expected_conditions as EC
import requests


load_dotenv()

FORMATO_DATA_HORA = "%d/%m/%Y %H:%M"
FORMATO_DATA_HORA_REGISTRO = "%d/%m/%Y %H:%M:%S"


def tranformation(data_hora_inicial, data_hora_final, duracao_segundos):
    """
    Gera uma lista de datetimes entre data_hora_inicial e data_hora_final.
    O intervalo entre cada item é duracao_segundos + 60s (1 min de segurança).
    """
    inicio = datetime.strptime(data_hora_inicial, FORMATO_DATA_HORA)
    fim = datetime.strptime(data_hora_final, FORMATO_DATA_HORA)
    intervalo = timedelta(seconds=duracao_segundos + 60)

    datas = []
    atual = inicio

    while atual <= fim:
        datas.append(atual.strftime(FORMATO_DATA_HORA))
        atual += intervalo

    return datas


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

    # Cadeia de filtros para limpar o áudio (ambientes barulhentos) mantendo
    # a voz clara. Histórico do ajuste, para referência:
    #  - 1ª versão (sem filtro): chiado do 3gp.
    #  - 2ª versão (equalizer de pico em 2500Hz + dynaudnorm rápido): corrigiu
    #    o chiado mas criou um zunido tipo microfonia (o pico estreito do EQ,
    #    somado a um dynaudnorm reagindo rápido demais, gerava um tom
    #    ressonante constante).
    #  - 3ª versão (removeu o EQ, afftdn mais agressivo nf=-30): removeu o
    #    zunido mas abafou a voz (denoise agressivo demais suaviza justamente
    #    as frequências de 4-7kHz onde vivem consoantes como "s"/"f", que dão
    #    nitidez à fala).
    #  - Versão atual: denoise adaptativo mais moderado (nf=-27, tn=1 rastreia
    #    o ruído em vez de usar um alvo fixo), lowpass mais alto (7000 em vez
    #    de 4000) para preservar as consoantes, e brilho reintroduzido via
    #    "treble" (shelving, reforço amplo e suave) em vez de um EQ de pico
    #    estreito — evita o efeito de tom ressonante que causou o zunido.
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


def salvar_gravacoes():
    navegador = None
    download_dir = None
    try:
        print("Iniciando gravacao...")
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
        print("Clicou no link de gravacao")
        sleep(2)

        navegador.find_element(
            By.CSS_SELECTOR, "#load_div > div:nth-child(11) > a"
        ).click()
        print("Acessou a página de gravação")

        _definir_registros_por_pagina(navegador, "50")

        # Recupera os links de download e a data/hora de cada gravação, de todas as páginas
        dados = _coletar_dados_todas_paginas(navegador)

        # Baixa os arquivos usando a sessão do navegador, renomeando cada um
        # para a data/hora da gravação (formato dia_mes_hora_min).
        _baixar_com_sessao(navegador, download_dir, dados)

        # Converte todos os .3gp baixados para .mp3
        _converter_3gp_para_mp3(download_dir)

        # Compacta as pastas por dia em um único audios.zip
        _zipar_audios(download_dir)

        sleep(1000)

        return "Gravação concluída com sucesso!"

    except Exception as e:
        print(f"❌ ERRO: {str(e)}")
    finally:
        if navegador:
            try:
                navegador.quit()
            except Exception:
                pass

if __name__ == "__main__":
    data_hora_inicial = "01/01/2024 00:00"
    data_hora_final = "01/01/2024 00:10"
    duracao_segundos = 60

    salvar_gravacoes()