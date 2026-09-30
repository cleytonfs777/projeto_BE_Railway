import os
import shutil
import threading
import zipfile
from datetime import datetime
from time import sleep
from urllib.parse import urljoin

from dotenv import load_dotenv
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException
from webdriver_manager.chrome import ChromeDriverManager
import requests

load_dotenv()

FORMATO_DATA_HORA_REGISTRO = "%d/%m/%Y %H:%M:%S"

# Tudo fica dentro da mesma pasta "downloads" usada pelo job de áudios
# (mesmo volume persistente/gitignore), mas isolado em sua própria
# subpasta para as duas automações não apagarem os arquivos uma da outra.
DOWNLOADS_ROOT = os.path.abspath("downloads")
VIDEOS_ROOT = os.path.join(DOWNLOADS_ROOT, "videos")
DADOS_DIR = os.path.join(VIDEOS_ROOT, "dados")
PREFIXO_ZIP = "videos_parte"
TAMANHO_MAXIMO_ZIP_BYTES = 100 * 1024 * 1024
TAMANHO_MINIMO_MP4_BYTES = 2048

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


def _destino_video(data_hora_texto, extensao=".mp4"):
    """
    Monta o caminho final do vídeo: DADOS_DIR/<dia_mes>/<dd_mm_HH_MM_SS>.mp4
    """
    dt = _parse_data_hora_whatsapp(data_hora_texto)

    if dt is None:
        pasta_dia = os.path.join(DADOS_DIR, "sem_data")
        nome = f"sem_data{extensao}"
    else:
        pasta_dia = os.path.join(DADOS_DIR, dt.strftime("%d_%m"))
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


def _extensao_do_href(href, fallback=".mp4"):
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
        {
            "User-Agent": navegador.execute_script("return navigator.userAgent"),
            "Referer": navegador.current_url,
        }
    )
    return sessao


def _arquivo_parece_mp4_valido(caminho):
    """Checagem rápida: tamanho mínimo + assinatura ftyp (não é HTML/JSON)."""
    try:
        tamanho = os.path.getsize(caminho)
    except OSError:
        return False, "arquivo inexistente"

    if tamanho < TAMANHO_MINIMO_MP4_BYTES:
        return False, f"arquivo muito pequeno ({tamanho} bytes)"

    with open(caminho, "rb") as f:
        cabecalho = f.read(64)

    if not cabecalho:
        return False, "arquivo vazio"

    inicio = cabecalho.lstrip().lower()
    if inicio.startswith((b"<!doctype", b"<html", b"<?xml", b"{", b"[")):
        return False, "resposta HTML/JSON em vez de vídeo"

    if b"ftyp" not in cabecalho:
        return False, "sem assinatura ftyp (download incompleto/corrompido)"

    return True, "ok"


def _baixar_arquivo_com_retry(sessao, href, destino, tentativas=3):
    """Baixa e valida o mp4; em falha, remove e tenta de novo."""
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

            ok, detalhe = _arquivo_parece_mp4_valido(destino)
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
    # os vídeos capturados de tela chegam como "sent" (enviados pelo
    # próprio aparelho), não "received" como os áudios de voz — por isso
    # o seletor não restringe essa classe.
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

    for _ in range(tentativas_max):
        scroll_antes, scroll_depois = _rolar_conversa_para_cima(navegador)
        _, vals_depois = _vals_mensagens_visiveis(navegador)
        novas = set(vals_depois) - set_antes
        if novas:
            return True
        if scroll_depois <= 0 and scroll_antes <= 0:
            break
        if scroll_depois == scroll_antes and scroll_depois <= 0:
            break

    return False


def _videos_pendentes(navegador, vals_processados):
    """Retorna os vals de mensagens visíveis que ainda não foram processadas."""
    mensagens, _ = _vals_mensagens_visiveis(navegador)
    return [
        m.get_attribute("val")
        for m in mensagens
        if m.get_attribute("val") not in vals_processados
    ]


def _conferir_nenhum_video_restante(navegador, vals_processados, tentativas_max=5):
    """
    Antes de encerrar de vez, confirma que realmente não sobrou nenhum vídeo
    pendente na página. Às vezes, logo após apagar uma mensagem, o
    carregamento sob demanda demora um instante para refletir a lista
    atualizada, e um vídeo acaba "ficando para trás" se confiarmos numa
    única checagem. Por isso repetimos a conferência algumas vezes, rolando
    para cima entre as tentativas. Retorna True quando confirmar que não
    sobrou nada.
    """
    for tentativa in range(tentativas_max):
        sleep(1)
        pendentes = _videos_pendentes(navegador, vals_processados)
        if pendentes:
            yield (
                f"Conferência encontrou {len(pendentes)} vídeo(s) pendente(s) "
                f"que quase ficaram para trás (tentativa {tentativa + 1})."
            )
            return False

        if not _carregar_mais_antigas(navegador, tentativas_max=2):
            break

    return not _videos_pendentes(navegador, vals_processados)


def _clicar_filtro_video_tela(navegador, timeout=20):
    """
    Tenta localizar e clicar no filtro "Video Capturado da Tela".
    Retorna True se o filtro existia e foi clicado, ou False se ele não
    estiver presente nesta página (algumas páginas não têm esse filtro).
    """
    try:
        span_video_tela = WebDriverWait(navegador, timeout).until(
            EC.element_to_be_clickable(
                (
                    By.XPATH,
                    "//span[contains(@class,'name-meta') and normalize-space()='Video Capturado da Tela']",
                )
            )
        )
    except TimeoutException:
        return False

    _clicar_com_seguranca(navegador, span_video_tela)
    return True


def _baixar_videos_pagina(navegador, empacotador, precisa_filtrar):
    """
    Gerador: baixa e apaga os vídeos da página de mídia atualmente aberta,
    empacotando cada arquivo baixado progressivamente. Emite mensagens de
    progresso e, ao final, retorna (baixados, falhas) via `return` (use
    `resultado = yield from ...` para capturar).

    Se precisa_filtrar for True e o filtro "Video Capturado da Tela" não
    existir nesta página, pula a página sem baixar nada (retorna (0, 0)).
    Se precisa_filtrar for False, baixa direto sem procurar esse filtro
    (usado na 2ª página, que já mostra só vídeos).
    """
    if precisa_filtrar:
        if not _clicar_filtro_video_tela(navegador):
            yield (
                "Filtro 'Video Capturado da Tela' não encontrado nesta "
                "página — pulando."
            )
            return 0, 0
        yield "Clicou em 'Video Capturado da Tela'"

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

            # Antes de encerrar de vez, confere novamente se não sobrou
            # nenhum link de vídeo na página (evita vídeos "ficarem para
            # trás" por causa do carregamento sob demanda).
            confirmado_limpo = yield from _conferir_nenhum_video_restante(
                navegador, vals_processados
            )
            if not confirmado_limpo:
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
                _destino_video(data_hora_texto, extensao)
            )
            relativo = os.path.relpath(destino, DADOS_DIR)

            yield f"Baixando: {relativo} ({data_hora_texto})"
            ok, detalhe = _baixar_arquivo_com_retry(sessao, href_abs, destino)
            if not ok:
                falhas += 1
                vals_processados.add(val)
                yield f"  ❌ Download inválido ({detalhe}): {relativo}"
                continue

            yield f"Download OK, apagando mensagem {val}..."
            _apagar_mensagem_conversa(navegador, mensagem, val)
            vals_processados.add(val)
            baixados += 1

            fechado = empacotador.adicionar(destino)
            if fechado:
                nome, mb = fechado
                yield (
                    f"📦 Pacote pronto: {nome} ({mb:.1f} MB) — "
                    "já disponível para download"
                )

            processados_desde_scroll += 1
            if processados_desde_scroll >= SCROLL_A_CADA:
                yield "Scroll periódico ↑ para carregar vídeos mais antigos..."
                _carregar_mais_antigas(navegador, tentativas_max=2)
                processados_desde_scroll = 0

        except Exception as e:
            falhas += 1
            vals_processados.add(val)
            yield f"❌ Falha na mensagem {val}: {e}"
            continue

    yield f"Página: {baixados} baixado(s), {falhas} falha(s)."
    return baixados, falhas


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

    def _caminho_parcial_arquivo(self, n):
        return os.path.join(VIDEOS_ROOT, f"{PREFIXO_ZIP}{n:02d}.zip.partial")

    def _abrir(self):
        os.makedirs(VIDEOS_ROOT, exist_ok=True)
        self.caminho_parcial = self._caminho_parcial_arquivo(self.contador)
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
    Lista os zips de vídeo completos em VIDEOS_ROOT (ignora .partial).
    """
    if not os.path.isdir(VIDEOS_ROOT):
        return []

    resultado = []
    for nome in sorted(os.listdir(VIDEOS_ROOT)):
        if not (nome.startswith(PREFIXO_ZIP) and nome.endswith(".zip")):
            continue
        if nome.endswith(".partial"):
            continue
        caminho = os.path.join(VIDEOS_ROOT, nome)
        if os.path.isfile(caminho):
            resultado.append(
                {"nome": nome, "tamanho_bytes": os.path.getsize(caminho)}
            )
    return resultado


def caminho_zip_seguro(nome_arquivo):
    """
    Resolve o caminho absoluto de um zip de vídeo a partir do nome
    informado pelo cliente, validando o formato esperado para evitar path
    traversal. Retorna None se o nome for inválido ou o arquivo não existir.
    """
    nome = os.path.basename(nome_arquivo or "")
    if not (nome.startswith(PREFIXO_ZIP) and nome.endswith(".zip")):
        return None
    if nome.endswith(".partial"):
        return None

    caminho = os.path.join(VIDEOS_ROOT, nome)
    if not os.path.isfile(caminho):
        return None

    return caminho


def _esvaziar_pasta_downloads():
    """
    Remove os dados de trabalho (DADOS_DIR) e os zips de vídeo já gerados
    dentro de VIDEOS_ROOT. Não mexe em outras pastas que dividam a mesma
    raiz "downloads" (ex.: os áudios), já que ela é compartilhada entre as
    diferentes automações.
    """
    if os.path.isdir(DADOS_DIR):
        shutil.rmtree(DADOS_DIR, ignore_errors=True)

    if os.path.isdir(VIDEOS_ROOT):
        for nome in os.listdir(VIDEOS_ROOT):
            if nome.startswith(PREFIXO_ZIP) and (
                nome.endswith(".zip") or nome.endswith(".zip.partial")
            ):
                try:
                    os.remove(os.path.join(VIDEOS_ROOT, nome))
                except OSError:
                    pass

    os.makedirs(DADOS_DIR, exist_ok=True)


def baixar_videos():
    """
    Gerador que faz login no BE, baixa e apaga os vídeos capturados de
    tela das duas páginas de mídia, empacotando progressivamente em zips
    de ~100 MB, emitindo mensagens de progresso (SSE / job em background).
    """
    navegador = None
    try:
        yield "Esvaziando pasta de downloads de vídeos..."
        _esvaziar_pasta_downloads()

        yield "Iniciando download de vídeos..."

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
            By.CSS_SELECTOR, "#slide-out > li:nth-child(3) > a"
        ).click()
        yield "Clicou no painel antigo"
        sleep(2)

        empacotador = _EmpacotadorProgressivo()

        navegador.find_element(
            By.CSS_SELECTOR, "#load_div > div:nth-child(4) > a"
        ).click()
        yield "Acessou a primeira página de vídeos"

        # Na 1ª página, se o filtro "Video Capturado da Tela" não existir,
        # pula direto para a 2ª página de vídeos sem baixar nada aqui.
        baixados_1, falhas_1 = yield from _baixar_videos_pagina(
            navegador, empacotador, precisa_filtrar=True
        )

        navegador.find_element(
            By.CSS_SELECTOR, "#load_div > div:nth-child(14) > a"
        ).click()
        yield "Acessou a segunda página de vídeos"

        # Na 2ª página não existe o filtro "Video Capturado da Tela": baixa
        # direto, sem tentar localizar/clicar nesse texto.
        baixados_2, falhas_2 = yield from _baixar_videos_pagina(
            navegador, empacotador, precisa_filtrar=False
        )

        try:
            navegador.quit()
        except Exception:
            pass
        navegador = None
        yield "Navegador fechado — finalizando empacotamento..."

        fechado_final = empacotador.finalizar()
        if fechado_final:
            nome, mb = fechado_final
            yield (
                f"📦 Pacote pronto: {nome} ({mb:.1f} MB) — "
                "já disponível para download"
            )

        baixados = baixados_1 + baixados_2
        falhas = falhas_1 + falhas_2

        zips = listar_zips_disponiveis()
        if zips:
            yield f"Pacotes disponíveis: {len(zips)}"
            for item in zips:
                mb = item["tamanho_bytes"] / (1024 * 1024)
                yield f"  - {item['nome']} ({mb:.1f} MB)"
        else:
            yield "Nenhum pacote zip gerado."

        yield (
            f"Download de vídeos concluído! {baixados} baixado(s), "
            f"{falhas} falha(s)."
        )

    except Exception as e:
        yield f"❌ ERRO: {str(e)}"
    finally:
        if navegador:
            try:
                navegador.quit()
            except Exception:
                pass


def status_job_videos():
    with _job_lock:
        return {
            "status": _job_status,
            "log": list(_job_log),
        }


def iniciar_job_videos():
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
            for msg in baixar_videos():
                with _job_lock:
                    _job_log.append(msg)
            with _job_lock:
                _job_status = "done"
        except Exception as e:
            with _job_lock:
                _job_log.append(f"❌ ERRO: {e}")
                _job_status = "error"

    _job_thread = threading.Thread(target=_run, daemon=True, name="baixar-videos")
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
