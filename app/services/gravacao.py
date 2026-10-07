import os
from datetime import datetime, timedelta
from time import sleep

from dotenv import load_dotenv
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import Select, WebDriverWait
from webdriver_manager.chrome import ChromeDriverManager

load_dotenv()

FORMATO_DATA_HORA = "%d/%m/%Y %H:%M"

SE_COMPRA = os.getenv("SE_COMPRA")

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
    return navegador


def registrar_gravacao(data_hora_inicial, data_hora_final, duracao_segundos):
    navegador = None
    try:
        yield "Iniciando gravacao..."

        navegador = _criar_navegador()
        WebDriverWait(navegador, 20)

        user = os.getenv("USER_BE")
        password = os.getenv("PASSWORD_BE")
        url = os.getenv("URL_BE")

        datas = tranformation(data_hora_inicial, data_hora_final, duracao_segundos)
        yield str(datas)

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

        # STR_COMPRA = f"#slide-out > li:nth-child({SE_COMPRA}) > a"

        # navegador.find_element(
        #     By.CSS_SELECTOR, STR_COMPRA).click()
        # yield "Clicou no link de gravacao"

        # sleep(2)

        navegador.find_element(
            By.CSS_SELECTOR, "#load_div > div:nth-child(11) > a"
        ).click()
        yield "Clicou no botao de load"

        for data in datas:
            yield f"Gravando para a data: {data}"

            select = Select(navegador.find_element(By.CSS_SELECTOR, "#type"))
            select.select_by_value("1")
            yield "Selecionou o valor 1 no select de type"
            sleep(1)

            navegador.execute_script(
                'document.querySelector("#startTime").value = arguments[0];',
                data,
            )
            yield "Setou o valor de startTime"
            sleep(1)

            duration_input = navegador.find_element(By.CSS_SELECTOR, "#duration")
            duration_input.clear()
            duration_input.send_keys(str(duracao_segundos))
            yield "Setou o valor de duration"
            sleep(1)

            navegador.find_element(By.ID, "searchbutton").click()
            yield "Clicou no botão de gravar"

            sleep(1)

        sleep(1)
        yield "Gravação concluída com sucesso!"

    except Exception as e:
        yield f"❌ ERRO: {str(e)}"
    finally:
        if navegador:
            try:
                navegador.quit()
            except Exception:
                pass
