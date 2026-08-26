import json
import os
from pathlib import Path

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Form, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from app import db
from app.auth import exigir_login_api, exigir_login_pagina, usuario_logado
from app.services.gravacao import registrar_gravacao
from app.services.baixar_audios import (
    baixar_audios,
    caminho_zip_seguro,
    listar_zips_disponiveis,
)

load_dotenv()

app = FastAPI(title="Registro de Gravação BE")

SECRET_KEY = os.getenv("SECRET_KEY")
if not SECRET_KEY:
    raise RuntimeError(
        "SECRET_KEY não definida. Configure essa variável no .env "
        "(necessária para assinar as sessões de login)."
    )

app.add_middleware(SessionMiddleware, secret_key=SECRET_KEY)

BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


@app.on_event("startup")
def _preparar_banco():
    db.inicializar_banco()


@app.get("/login", response_class=HTMLResponse)
async def login_form(request: Request, erro: bool = False):
    if usuario_logado(request):
        return RedirectResponse(url="/", status_code=303)
    return templates.TemplateResponse(
        "login.html", {"request": request, "erro": erro}
    )


@app.post("/login")
async def login_submit(
    request: Request, username: str = Form(...), senha: str = Form(...)
):
    if db.verificar_credenciais(username, senha):
        request.session["usuario"] = username
        return RedirectResponse(url="/", status_code=303)
    return RedirectResponse(url="/login?erro=1", status_code=303)


@app.get("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse(url="/login", status_code=303)


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    redirecionamento = exigir_login_pagina(request)
    if redirecionamento:
        return redirecionamento

    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "formato_data_hora": "DD/MM/AAAA HH:MM",
            "usuario": usuario_logado(request),
        },
    )


@app.get("/baixar-audios", response_class=HTMLResponse)
async def baixar_audios_pagina(request: Request):
    redirecionamento = exigir_login_pagina(request)
    if redirecionamento:
        return redirecionamento

    return templates.TemplateResponse(
        "baixar_audios.html",
        {"request": request, "usuario": usuario_logado(request)},
    )


@app.get("/api/gerar")
async def gerar(
    data_hora_inicial: str,
    data_hora_final: str,
    duracao_segundos: int,
    _usuario: str = Depends(exigir_login_api),
):
    def event_stream():
        for message in registrar_gravacao(
            data_hora_inicial, data_hora_final, duracao_segundos
        ):
            payload = json.dumps({"message": message}, ensure_ascii=False)
            yield f"data: {payload}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/api/baixar-audios")
async def api_baixar_audios(_usuario: str = Depends(exigir_login_api)):
    def event_stream():
        for message in baixar_audios():
            payload = json.dumps({"message": message}, ensure_ascii=False)
            yield f"data: {payload}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/api/baixar-audios/arquivos")
async def api_baixar_audios_arquivos(_usuario: str = Depends(exigir_login_api)):
    return [
        {
            "nome": item["nome"],
            "tamanho_mb": round(item["tamanho_bytes"] / (1024 * 1024), 1),
        }
        for item in listar_zips_disponiveis()
    ]


@app.get("/api/baixar-audios/arquivo/{nome_arquivo}")
async def api_baixar_audios_arquivo(
    nome_arquivo: str, _usuario: str = Depends(exigir_login_api)
):
    caminho = caminho_zip_seguro(nome_arquivo)
    if not caminho:
        return HTMLResponse(
            "Arquivo não encontrado. Execute o download primeiro.",
            status_code=404,
        )
    return FileResponse(
        caminho, media_type="application/zip", filename=os.path.basename(caminho)
    )
