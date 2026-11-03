"""Envio de e-mail pela conta SMTP (Office 365).

Configuração pelo .env da raiz do projeto (ver .env.example). Com --dry-run
(dry_run=True) nada sai: a mensagem é gravada em outbox/ para conferência.
"""

import os
import smtplib
from datetime import datetime
from email.message import EmailMessage
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUTBOX = ROOT / "outbox"


def load_env(path=ROOT / ".env"):
    """Lê o .env sem depender de pacote externo; não sobrescreve o ambiente."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


def build_message(to, subject, body):
    load_env()
    msg = EmailMessage()
    msg["From"] = os.getenv("MAIL_FROM", "avisos@example.com")
    msg["To"] = ", ".join(to) if isinstance(to, (list, tuple)) else to
    msg["Subject"] = subject
    reply_to = os.getenv("MAIL_REPLY_TO", "")
    if reply_to:
        msg["Reply-To"] = reply_to
    msg.set_content(body)
    return msg


def send(to, subject, body, dry_run=False):
    """Envia (ou grava no outbox) e devolve uma descrição do que aconteceu."""
    msg = build_message(to, subject, body)

    if dry_run:
        OUTBOX.mkdir(exist_ok=True)
        slug = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        dest = OUTBOX / f"{slug}.eml"
        dest.write_bytes(bytes(msg))
        return f"dry-run: {dest.relative_to(ROOT)}"

    host = os.getenv("SMTP_HOST", "")
    password = os.getenv("SMTP_PASSWORD", "")
    username = os.getenv("SMTP_USERNAME", "")
    if not host or not password:
        raise RuntimeError("SMTP_HOST/SMTP_PASSWORD não configurados no .env")

    port = int(os.getenv("SMTP_PORT", "587"))
    with smtplib.SMTP(host, port, timeout=60) as smtp:
        if os.getenv("SMTP_USE_TLS", "1").lower() in {"1", "true", "yes", "on"}:
            smtp.starttls()
        smtp.login(username, password)
        smtp.send_message(msg)
    return f"enviado para {msg['To']}"
