import os
import shutil
import subprocess
import threading
import zipfile
from datetime import datetime
from time import sleep
from urllib.parse import urljoin

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
TAMANHO_MAXIMO_ZIP_BYTES = 100 * 1024 * 1024

# Job em background: sobrevive a queda de rede do navegador
_job_lock = threading.Lock()
_job_status = "idle"  # idle | running | done | error
_job_log = []
_job_thread = None


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


TAMANHO_MINIMO_3GP_BYTES = 2048


def _arquivo_parece_3gp_cabecalho(caminho):
    """Checagem rápida: tamanho mínimo + assinatura ftyp (não é HTML)."""
    try:
        tamanho = os.path.getsize(caminho)
    except OSError:
        return False, "arquivo inexistente"

    if tamanho < TAMANHO_MINIMO_3GP_BYTES:
        return False, f"arquivo muito pequeno ({tamanho} bytes)"

    with open(caminho, "rb") as f:
        cabecalho = f.read(64)

    if not cabecalho:
        return False, "arquivo vazio"

    inicio = cabecalho.lstrip().lower()
    if inicio.startswith((b"<!doctype", b"<html", b"<?xml", b"{", b"[")):
        return False, "resposta HTML/JSON em vez de áudio"

    if b"ftyp" not in cabecalho:
        return False, "sem assinatura ftyp (download incompleto/corrompido)"

    return True, "ok"


def _arquivo_parece_3gp_valido(caminho):
    """
    Valida o 3gp de forma mais estrita (ffprobe). Sem moov, o ffmpeg falha
    com 'moov atom not found'.
    """
    ok, detalhe = _arquivo_parece_3gp_cabecalho(caminho)
    if not ok:
        return False, detalhe

    try:
        probe = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                caminho,
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=20,
        )
        if probe.returncode != 0:
            err = (probe.stderr or b"").decode(errors="ignore").strip()
            return False, _resumir_erro_ffmpeg(err) or "ffprobe rejeitou o arquivo"
    except FileNotFoundError:
        pass
    except Exception as e:
        return False, f"ffprobe falhou: {e}"

    return True, "ok"


def _resumir_erro_ffmpeg(stderr_texto, max_len=280):
    """Extrai a linha útil do stderr do ffmpeg (sem o banner longo)."""
    linhas = [
        ln.strip()
        for ln in (stderr_texto or "").splitlines()
        if ln.strip()
        and not ln.startswith("ffmpeg version")
        and not ln.startswith("built with")
        and not ln.startswith("configuration:")
        and not ln.startswith("libav")
        and not ln.startswith("libsw")
        and not ln.startswith("libpost")
    ]
    if not linhas:
        return (stderr_texto or "erro desconhecido")[:max_len]
    for ln in reversed(linhas):
        low = ln.lower()
        if "error" in low or "invalid" in low or "not found" in low or "failed" in low:
            return ln[:max_len]
    return " | ".join(linhas[-3:])[:max_len]


def _baixar_arquivo_com_retry(sessao, href, destino, tentativas=3):
    """Baixa e valida o 3gp; em falha, remove e tenta de novo."""
    ultimo_erro = "falha desconhecida"
    for tentativa in range(1, tentativas + 1):
        try:
            if os.path.exists(destino):
                os.remove(destino)

            r = sessao.get(href, stream=True, timeout=120)
            r.raise_for_status()

            content_type = (r.headers.get("Content-Type") or "").lower()
            if "text/html" in content_type or "application/json" in content_type:
                ultimo_erro = f"Content-Type inesperado: {content_type}"
                r.close()
                sleep(0.8 * tentativa)
                continue

            esperado = r.headers.get("Content-Length")
            escrito = 0
            with open(destino, "wb") as f:
                for chunk in r.iter_content(8192):
                    if chunk:
                        f.write(chunk)
                        escrito += len(chunk)

            if esperado is not None:
                try:
                    if escrito != int(esperado):
                        ultimo_erro = (
                            f"tamanho incompleto ({escrito}/{esperado} bytes)"
                        )
                        if os.path.exists(destino):
                            os.remove(destino)
                        sleep(0.8 * tentativa)
                        continue
                except ValueError:
                    pass

            ok, detalhe = _arquivo_parece_3gp_cabecalho(destino)
            if ok:
                return True, "ok"

            ultimo_erro = detalhe
            if os.path.exists(destino):
                os.remove(destino)
            sleep(0.8 * tentativa)
        except Exception as e:
            ultimo_erro = str(e)
            if os.path.exists(destino):
                try:
                    os.remove(destino)
                except OSError:
                    pass
            sleep(0.8 * tentativa)

    return False, ultimo_erro


def _baixar_com_sessao(navegador, dados):
    """
    Baixa cada arquivo em uma subpasta de DADOS_DIR nomeada pelo dia da
    gravação (formato dia_mes), renomeando o arquivo pela data/hora.
    Valida o conteúdo (evita .3gp corrompidos que quebram o ffmpeg).
    """
    sessao = requests.Session()
    for c in navegador.get_cookies():
        sessao.cookies.set(c["name"], c["value"])
    sessao.headers.update(
        {
            "User-Agent": navegador.execute_script("return navigator.userAgent"),
            "Referer": navegador.current_url,
        }
    )

    base_url = navegador.current_url
    falhas_download = 0

    for i, item in enumerate(dados, 1):
        href = item["href"]
        href_abs = urljoin(base_url, href)
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

        relativo = os.path.relpath(destino, DADOS_DIR)
        yield f"Baixando {i}/{len(dados)}: {relativo}"
        ok, detalhe = _baixar_arquivo_com_retry(sessao, href_abs, destino)
        if not ok:
            falhas_download += 1
            yield f"  ❌ Download inválido ({detalhe}): {relativo}"

    if falhas_download:
        yield (
            f"Downloads concluídos com {falhas_download} falha(s) "
            "(arquivos inválidos foram descartados)."
        )
    else:
        yield "Downloads concluídos!"


def _converter_e_empacotar_progressivo():
    """
    Converte cada .3gp para .mp3 e, conforme os arquivos ficam prontos,
    empacota em zips de até ~100 MB. Cada pacote fechado já fica disponível
    para download enquanto o restante continua convertendo.
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

    yield (
        f"Convertendo {len(arquivos_3gp)} arquivo(s) e empacotando "
        f"em partes de até {TAMANHO_MAXIMO_ZIP_BYTES // (1024 * 1024)} MB..."
    )

    filtro_audio = (
        "highpass=f=100,"
        "lowpass=f=7000,"
        "afftdn=nf=-27:tn=1,"
        "dynaudnorm=f=500:g=31,"
        "treble=g=4:f=3000,"
        "alimiter=limit=0.95"
    )

    empacotador = _EmpacotadorProgressivo()
    convertidos = 0
    mantidos_3gp = 0
    pacotes = 0

    def _empacotar_e_avisar(caminho_pronto):
        nonlocal pacotes
        fechado = empacotador.adicionar(caminho_pronto)
        if fechado:
            nome, mb = fechado
            pacotes += 1
            yield (
                f"📦 Pacote pronto: {nome} ({mb:.1f} MB) — "
                "já disponível para download"
            )

    for i, origem in enumerate(arquivos_3gp, 1):
        destino_mp3 = os.path.splitext(origem)[0] + ".mp3"
        nome = os.path.relpath(origem, DADOS_DIR)

        yield f"Convertendo {i}/{len(arquivos_3gp)}: {nome}"

        tentativas_ffmpeg = [
            [
                "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                "-i", origem,
                "-af", filtro_audio,
                "-ar", "44100", "-c:a", "libmp3lame", "-qscale:a", "0",
                destino_mp3,
            ],
            [
                "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                "-err_detect", "ignore_err",
                "-i", origem,
                "-vn", "-ar", "44100", "-c:a", "libmp3lame", "-qscale:a", "4",
                destino_mp3,
            ],
            [
                "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                "-f", "amr", "-i", origem,
                "-ar", "8000", "-c:a", "libmp3lame", "-qscale:a", "4",
                destino_mp3,
            ],
        ]

        convertido = False
        ultimo_erro = ""
        for cmd in tentativas_ffmpeg:
            if os.path.exists(destino_mp3):
                try:
                    os.remove(destino_mp3)
                except OSError:
                    pass
            resultado = subprocess.run(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE
            )
            if (
                resultado.returncode == 0
                and os.path.isfile(destino_mp3)
                and os.path.getsize(destino_mp3) > 0
            ):
                convertido = True
                break
            ultimo_erro = _resumir_erro_ffmpeg(
                resultado.stderr.decode(errors="ignore")
            )

        if convertido:
            try:
                os.remove(origem)
            except OSError:
                pass
            convertidos += 1
            yield from _empacotar_e_avisar(destino_mp3)
            continue

        mantidos_3gp += 1
        if os.path.exists(destino_mp3):
            try:
                os.remove(destino_mp3)
            except OSError:
                pass
        yield (
            f"  ⚠️ Sem mp3 para {nome} ({ultimo_erro or 'erro desconhecido'}) — "
            "empacotando .3gp"
        )
        yield from _empacotar_e_avisar(origem)

    fechado_final = empacotador.finalizar()
    if fechado_final:
        nome, mb = fechado_final
        pacotes += 1
        yield (
            f"📦 Pacote pronto: {nome} ({mb:.1f} MB) — "
            "já disponível para download"
        )

    yield (
        f"Conversão/empacotamento concluídos! "
        f"{convertidos} mp3, {mantidos_3gp} em .3gp, {pacotes} pacote(s)."
    )


class _EmpacotadorProgressivo:
    """
    Empacota arquivos em zips de até TAMANHO_MAXIMO_ZIP_BYTES.
    Escreve .zip.partial e só renomeia para .zip ao fechar a parte
    (assim o download só lista pacotes completos).
    """

    def __init__(self):
        self.contador = 1
        self.zip_atual = None
        self.caminho_parcial = None
        self.tamanho_atual = 0

    def _caminho_parcial(self, n):
        return os.path.join(
            DOWNLOADS_ROOT, f"{PREFIXO_ZIP}{n:02d}.zip.partial"
        )

    def _abrir(self):
        os.makedirs(DOWNLOADS_ROOT, exist_ok=True)
        self.caminho_parcial = self._caminho_parcial(self.contador)
        self.zip_atual = zipfile.ZipFile(
            self.caminho_parcial, "w", zipfile.ZIP_DEFLATED
        )
        self.tamanho_atual = 0

    def _fechar_parte(self):
        if self.zip_atual is None:
            return None
        self.zip_atual.close()
        self.zip_atual = None
        final = self.caminho_parcial[: -len(".partial")]
        os.replace(self.caminho_parcial, final)
        mb = os.path.getsize(final) / (1024 * 1024)
        nome = os.path.basename(final)
        self.contador += 1
        self.caminho_parcial = None
        self.tamanho_atual = 0
        return nome, mb

    def adicionar(self, caminho_arquivo):
        """
        Inclui o arquivo no zip atual. Se estourar o limite, fecha a parte
        e abre outra. Remove o arquivo de origem após incluir.
        Retorna (nome, mb) se uma parte foi fechada neste passo, senão None.
        """
        if not os.path.isfile(caminho_arquivo):
            return None

        tamanho = os.path.getsize(caminho_arquivo)
        pronto = None

        if self.zip_atual is None:
            self._abrir()

        if (
            self.tamanho_atual > 0
            and self.tamanho_atual + tamanho > TAMANHO_MAXIMO_ZIP_BYTES
        ):
            pronto = self._fechar_parte()
            self._abrir()

        arcname = os.path.relpath(caminho_arquivo, DADOS_DIR)
        self.zip_atual.write(caminho_arquivo, arcname)
        self.tamanho_atual += tamanho
        try:
            os.remove(caminho_arquivo)
        except OSError:
            pass

        # limpa pasta do dia se vazia
        pasta = os.path.dirname(caminho_arquivo)
        try:
            if pasta.startswith(DADOS_DIR) and not os.listdir(pasta):
                os.rmdir(pasta)
        except OSError:
            pass

        return pronto

    def finalizar(self):
        if self.zip_atual is None:
            return None
        if self.tamanho_atual <= 0:
            self.zip_atual.close()
            self.zip_atual = None
            if self.caminho_parcial and os.path.exists(self.caminho_parcial):
                try:
                    os.remove(self.caminho_parcial)
                except OSError:
                    pass
            return None
        return self._fechar_parte()


def listar_zips_disponiveis():
    """
    Lista os zips de áudio completos em DOWNLOADS_ROOT (ignora .partial).
    """
    if not os.path.isdir(DOWNLOADS_ROOT):
        return []

    resultado = []
    for nome in sorted(os.listdir(DOWNLOADS_ROOT)):
        if not (nome.startswith(PREFIXO_ZIP) and nome.endswith(".zip")):
            continue
        if nome.endswith(".partial"):
            continue
        caminho = os.path.join(DOWNLOADS_ROOT, nome)
        if os.path.isfile(caminho):
            resultado.append(
                {"nome": nome, "tamanho_bytes": os.path.getsize(caminho)}
            )
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
    if nome.endswith(".partial"):
        return None

    caminho = os.path.join(DOWNLOADS_ROOT, nome)
    if not os.path.isfile(caminho):
        return None

    return caminho


def _esvaziar_pasta_downloads():
    """
    Remove os dados de trabalho (DADOS_DIR) e os zips de áudio já gerados
    (prefixo PREFIXO_ZIP) dentro de DOWNLOADS_ROOT. Não mexe em outros
    arquivos/pastas que dividam essa mesma raiz (ex.: pasta de vídeos),
    já que DOWNLOADS_ROOT é compartilhada entre as diferentes automações.
    """
    if os.path.isdir(DADOS_DIR):
        shutil.rmtree(DADOS_DIR, ignore_errors=True)

    if os.path.isdir(DOWNLOADS_ROOT):
        for nome in os.listdir(DOWNLOADS_ROOT):
            if nome.startswith(PREFIXO_ZIP) and (
                nome.endswith(".zip") or nome.endswith(".zip.partial")
            ):
                try:
                    os.remove(os.path.join(DOWNLOADS_ROOT, nome))
                except OSError:
                    pass

    os.makedirs(DADOS_DIR, exist_ok=True)


def baixar_audios():
    """
    Gerador que faz login no BE, coleta e baixa todas as gravações
    disponíveis, converte para mp3 e empacota progressivamente em zips
    de ~100 MB, emitindo mensagens de progresso (SSE / job em background).
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

        try:
            navegador.quit()
        except Exception:
            pass
        navegador = None
        yield "Navegador fechado — convertendo e empacotando..."

        yield from _converter_e_empacotar_progressivo()

        zips = listar_zips_disponiveis()
        if zips:
            yield f"Pacotes disponíveis: {len(zips)}"
            for item in zips:
                mb = item["tamanho_bytes"] / (1024 * 1024)
                yield f"  - {item['nome']} ({mb:.1f} MB)"
        else:
            yield "Nenhum pacote zip gerado."

        yield "Download de áudios concluído com sucesso!"

    except Exception as e:
        yield f"❌ ERRO: {str(e)}"
    finally:
        if navegador:
            try:
                navegador.quit()
            except Exception:
                pass


def status_job_audios():
    with _job_lock:
        return {
            "status": _job_status,
            "log": list(_job_log),
        }


def iniciar_job_audios():
    """
    Inicia o download em thread separada (não morre se o SSE cair).
    Retorna (iniciou_agora: bool, mensagem: str).
    """
    global _job_status, _job_thread, _job_log

    with _job_lock:
        if _job_status == "running":
            return False, "Já existe um download em andamento — reconectando ao log."
        _job_status = "running"
        _job_log = []

    def _run():
        global _job_status
        try:
            for msg in baixar_audios():
                with _job_lock:
                    _job_log.append(msg)
            with _job_lock:
                _job_status = "done"
        except Exception as e:
            with _job_lock:
                _job_log.append(f"❌ ERRO: {e}")
                _job_status = "error"

    _job_thread = threading.Thread(target=_run, daemon=True, name="baixar-audios")
    _job_thread.start()
    return True, "Job iniciado em background."


def iterar_log_job(desde=0, poll_s=0.4):
    """
    Gera mensagens do job a partir do índice `desde`, até o job terminar.
    Usado pelo SSE; se o cliente cair, o job continua na thread.
    """
    idx = desde
    while True:
        with _job_lock:
            status = _job_status
            novos = _job_log[idx:]
            idx = len(_job_log)

        for msg in novos:
            yield msg

        if status in ("done", "error") and not novos:
            with _job_lock:
                if len(_job_log) == idx and _job_status in ("done", "error"):
                    return
            continue

        sleep(poll_s)
