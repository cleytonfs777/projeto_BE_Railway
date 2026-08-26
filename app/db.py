import hashlib
import os
import secrets
import sqlite3
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = os.getenv("DB_PATH", str(BASE_DIR / "app.db"))

PBKDF2_ITERACOES = 600_000


def _conectar():
    conexao = sqlite3.connect(DB_PATH)
    conexao.row_factory = sqlite3.Row
    return conexao


def _hash_senha(senha: str, salt: str) -> str:
    return hashlib.pbkdf2_hmac(
        "sha256", senha.encode("utf-8"), salt.encode("utf-8"), PBKDF2_ITERACOES
    ).hex()


def _credenciais_admin_inicial():
    """
    Usuário/senha do admin inicial vêm só de variáveis de ambiente.
    Nunca hardcode senha no código (evita vazar no GitHub).
    """
    username = (os.getenv("ADMIN_USERNAME") or "").strip()
    senha = os.getenv("ADMIN_PASSWORD") or ""
    if not username or not senha:
        raise RuntimeError(
            "ADMIN_USERNAME e ADMIN_PASSWORD devem estar definidos no ambiente "
            "para criar o usuário admin na primeira inicialização."
        )
    return username, senha


def inicializar_banco():
    """
    Cria a tabela de usuários (se não existir) e garante a existência do
    usuário padrão, com a senha já armazenada como hash (nunca em texto).
    """
    with _conectar() as conexao:
        conexao.execute(
            """
            CREATE TABLE IF NOT EXISTS usuarios (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                senha_hash TEXT NOT NULL,
                salt TEXT NOT NULL
            )
            """
        )
        conexao.commit()

    username, senha = _credenciais_admin_inicial()
    if buscar_usuario(username) is None:
        criar_usuario(username, senha)


def buscar_usuario(username: str):
    with _conectar() as conexao:
        return conexao.execute(
            "SELECT * FROM usuarios WHERE username = ?", (username,)
        ).fetchone()


def criar_usuario(username: str, senha: str):
    salt = secrets.token_hex(16)
    senha_hash = _hash_senha(senha, salt)
    with _conectar() as conexao:
        conexao.execute(
            "INSERT INTO usuarios (username, senha_hash, salt) VALUES (?, ?, ?)",
            (username, senha_hash, salt),
        )
        conexao.commit()


def verificar_credenciais(username: str, senha: str) -> bool:
    usuario = buscar_usuario(username)
    if usuario is None:
        return False
    return secrets.compare_digest(
        _hash_senha(senha, usuario["salt"]), usuario["senha_hash"]
    )
