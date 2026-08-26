from fastapi import HTTPException, Request
from fastapi.responses import RedirectResponse


def usuario_logado(request: Request):
    return request.session.get("usuario")


def exigir_login_pagina(request: Request):
    """
    Para rotas de página: retorna um RedirectResponse para /login se não
    houver sessão ativa, ou None se o usuário já estiver autenticado.
    """
    if not usuario_logado(request):
        return RedirectResponse(url="/login", status_code=303)
    return None


def exigir_login_api(request: Request) -> str:
    """
    Dependência para rotas de API (SSE, download de arquivo): levanta 401
    se não houver sessão ativa.
    """
    usuario = usuario_logado(request)
    if not usuario:
        raise HTTPException(status_code=401, detail="Não autenticado")
    return usuario
