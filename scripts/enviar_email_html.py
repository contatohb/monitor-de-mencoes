#!/usr/bin/env python3
"""
enviar_email_html.py
====================
Envia email HTML via SMTP do Gmail usando App Password.

Configuração:
  - GMAIL_SMTP_USER : endereço Gmail (padrão: huddsong@gmail.com)
  - GMAIL_APP_PASSWORD : senha de aplicativo de 16 caracteres (sem espaços)

Uso como módulo:
    from enviar_email_html import enviar_email_html
    ok = enviar_email_html(to="dest@email.com", subject="Assunto", html_body="<html>...")

Uso direto:
    python3 enviar_email_html.py [--to dest@email.com] [--subject "Assunto"] [--html-file arquivo.html]
"""
from __future__ import annotations

import os
import smtplib
import sys
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import Optional

# ─────────────────────────────────────────────────────────────────────────────
# Configurações SMTP
# ─────────────────────────────────────────────────────────────────────────────

SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587  # STARTTLS

# Credenciais — lidas de variáveis de ambiente ou valores padrão
GMAIL_USER = os.getenv("GMAIL_SMTP_USER", "huddsong@gmail.com")
# App Password gerada em myaccount.google.com/apppasswords
# Armazenada sem espaços: "mwnv dnii uggt aikl" → "mwnvdniiuugtaikl"
_RAW_APP_PASS = os.getenv("GMAIL_APP_PASSWORD", "mwnvdniiuggtaikl")
GMAIL_APP_PASSWORD = _RAW_APP_PASS.replace(" ", "")


# ─────────────────────────────────────────────────────────────────────────────
# Funções auxiliares
# ─────────────────────────────────────────────────────────────────────────────

def _build_mime_message(
    to: str,
    subject: str,
    html_body: str,
    text_body: str = "",
    sender: str = "",
) -> MIMEMultipart:
    """
    Constrói uma mensagem MIME multipart/alternative com partes
    texto simples (fallback) e HTML.
    """
    if not sender:
        sender = GMAIL_USER

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["To"] = to
    msg["From"] = sender

    # Parte texto simples (fallback para clientes sem suporte a HTML)
    if not text_body:
        text_body = (
            "Este email contém conteúdo HTML. "
            "Por favor, utilize um cliente de email que suporte HTML para visualizá-lo."
        )
    msg.attach(MIMEText(text_body, "plain", "utf-8"))

    # Parte HTML (renderizada pelos clientes modernos)
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    return msg


def enviar_email_html(
    to: str,
    subject: str,
    html_body: str,
    text_body: str = "",
    sender: Optional[str] = None,
    smtp_user: Optional[str] = None,
    smtp_password: Optional[str] = None,
) -> bool:
    """
    Envia email HTML via SMTP do Gmail com App Password.

    Parâmetros
    ----------
    to : str
        Endereço de destino.
    subject : str
        Assunto do email.
    html_body : str
        Corpo HTML completo do email.
    text_body : str, opcional
        Versão texto simples (fallback). Gerada automaticamente se omitida.
    sender : str, opcional
        Endereço remetente. Usa GMAIL_USER se omitido.
    smtp_user : str, opcional
        Usuário SMTP. Usa GMAIL_USER se omitido.
    smtp_password : str, opcional
        App Password. Usa GMAIL_APP_PASSWORD se omitido.

    Retorna
    -------
    bool
        True se o email foi enviado com sucesso, False caso contrário.
    """
    _user = smtp_user or GMAIL_USER
    _password = (smtp_password or GMAIL_APP_PASSWORD).replace(" ", "")
    _sender = sender or _user

    msg = _build_mime_message(to, subject, html_body, text_body, _sender)

    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=30) as server:
            server.ehlo()
            server.starttls()
            server.ehlo()
            server.login(_user, _password)
            server.sendmail(_sender, [to], msg.as_bytes())
        return True
    except smtplib.SMTPAuthenticationError as e:
        print(
            f"[enviar_email_html] Erro de autenticação SMTP: {e}\n"
            "Verifique se a App Password está correta e se o 2FA está ativo na conta.",
            file=sys.stderr,
        )
        return False
    except smtplib.SMTPException as e:
        print(f"[enviar_email_html] Erro SMTP: {e}", file=sys.stderr)
        return False
    except Exception as e:
        print(f"[enviar_email_html] Erro inesperado: {e}", file=sys.stderr)
        return False


# ─────────────────────────────────────────────────────────────────────────────
# Execução direta (para testes)
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Testar envio de email HTML via SMTP")
    parser.add_argument("--to", default="huddsong@gmail.com", help="Destinatário")
    parser.add_argument("--subject", default="[TESTE] Email HTML — HB Advisory Intellicore")
    parser.add_argument("--html-file", help="Arquivo HTML para enviar")
    args = parser.parse_args()

    if args.html_file:
        html = Path(args.html_file).read_text(encoding="utf-8")
    else:
        html = """<!DOCTYPE html>
<html lang="pt-BR">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Teste Email HTML</title>
</head>
<body style="margin:0;padding:0;background:#F5F7FA;font-family:Arial,Helvetica,sans-serif;">
  <table width="100%" cellpadding="0" cellspacing="0" style="background:#F5F7FA;padding:32px 0;">
    <tr>
      <td align="center">
        <table width="600" cellpadding="0" cellspacing="0"
               style="background:#FFFFFF;border-radius:8px;overflow:hidden;
                      box-shadow:0 2px 8px rgba(0,0,0,0.08);">
          <!-- Cabeçalho -->
          <tr>
            <td style="background:#0D1B2A;padding:24px 32px;">
              <p style="margin:0;color:#FFFFFF;font-size:20px;font-weight:bold;
                        letter-spacing:0.5px;">HB Advisory Intellicore</p>
              <p style="margin:4px 0 0;color:#A0AEC0;font-size:13px;">
                Sistema de Monitoramento de Vagas Regulatórias
              </p>
            </td>
          </tr>
          <!-- Corpo -->
          <tr>
            <td style="padding:32px;">
              <h2 style="margin:0 0 16px;color:#0D1B2A;font-size:18px;">
                ✅ Teste de Email HTML — Sistema Funcionando
              </h2>
              <p style="color:#4A5568;font-size:14px;line-height:1.6;">
                Este é um email de teste do sistema de envio HTML via SMTP do Gmail.<br>
                Se você está vendo este email formatado corretamente, o sistema está
                operacional.
              </p>
              <table width="100%" cellpadding="12" cellspacing="0"
                     style="background:#F0FFF4;border-radius:6px;margin-top:20px;
                            border:1px solid #C6F6D5;">
                <tr>
                  <td>
                    <p style="margin:0;color:#276749;font-size:14px;font-weight:bold;">
                      Configuração SMTP ativa
                    </p>
                    <p style="margin:4px 0 0;color:#2F855A;font-size:13px;">
                      Protocolo: SMTP com STARTTLS · Porta: 587<br>
                      Autenticação: App Password (Gmail)
                    </p>
                  </td>
                </tr>
              </table>
            </td>
          </tr>
          <!-- Rodapé -->
          <tr>
            <td style="background:#F7FAFC;padding:16px 32px;border-top:1px solid #E2E8F0;">
              <p style="margin:0;color:#A0AEC0;font-size:12px;text-align:center;">
                HB Advisory · Sistema Intellicore · Vagas Regulatório Agro
              </p>
            </td>
          </tr>
        </table>
      </td>
    </tr>
  </table>
</body>
</html>"""

    print(f"Remetente : {GMAIL_USER}")
    print(f"Destinatário: {args.to}")
    print(f"Assunto   : {args.subject}")
    print("Enviando via SMTP...")

    ok = enviar_email_html(args.to, args.subject, html)
    print(f"Resultado : {'✅ Enviado com sucesso' if ok else '❌ Falhou — verifique o stderr'}")
    sys.exit(0 if ok else 1)
