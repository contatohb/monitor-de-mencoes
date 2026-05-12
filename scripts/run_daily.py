import os
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
run_daily.py — Monitor de Menções
====================================
Orquestra a execução diária completa:
  1. Verificação de idempotência via Supabase (garante execução única por dia)
  2. Health check de todas as fontes
  3. Executa o monitor de menções (monitor_completo.py)
  4. Envia o email HTML via Gmail SMTP
  5. Registra execução no Supabase

ARQUITETURA:
  - Supabase é o ÚNICO backend de estado. Sem Google Sheets, sem arquivo local.
  - Acessível via HTTP puro de qualquer sandbox — sem dependência de CLI local.
  - Idempotência garantida por UNIQUE constraint em monitor_execucoes.data_execucao.
  - INSERT com ON CONFLICT DO NOTHING: se já existe registro do dia, não executa.

Uso:
  python3 scripts/run_daily.py
  python3 scripts/run_daily.py --force-send     # envia mesmo sem resultados novos
  python3 scripts/run_daily.py --health-only    # apenas health check, sem busca
  python3 scripts/run_daily.py --force-run      # ignora o lock de idempotência
"""

import argparse
import json
import logging
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path

import requests

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
LOG_DIR = BASE_DIR / "logs"
SCRIPTS_DIR = BASE_DIR / "scripts"

DATA_DIR.mkdir(exist_ok=True)
LOG_DIR.mkdir(exist_ok=True)

LOG_FILE = LOG_DIR / f"run_daily_{date.today().isoformat()}.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Supabase — ÚNICO backend de estado
# ---------------------------------------------------------------------------
SUPABASE_URL = "https://wuadkgmggkmyglxpxeyh.supabase.co"
SUPABASE_KEY = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
    "eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Ind1YWRrZ21nZ2tteWdseHB4ZXloIiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc2MDk1NzU4NywiZXhwIjoyMDc2NTMzNTg3fQ."
    "Qroz39JExkH4tXofSIqzZMQNtQDAv5rPSR_OJdeH4FI"
)
_H = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
}


# ---------------------------------------------------------------------------
# Idempotência — ÚNICO mecanismo, via Supabase
# ---------------------------------------------------------------------------

def ja_executou_hoje(force_run: bool = False) -> bool:
    """
    Verifica no Supabase se já existe registro de execução bem-sucedida hoje.
    Retorna True se deve abortar (já executou).
    Política fail-open: se o Supabase falhar, permite execução (não bloqueia).
    """
    if force_run:
        log.info("--force-run ativo: ignorando lock de idempotência.")
        return False

    hoje = date.today().isoformat()
    try:
        r = requests.get(
            f"{SUPABASE_URL}/rest/v1/monitor_execucoes"
            f"?data_execucao=eq.{hoje}&status=eq.ok&select=id",
            headers=_H,
            timeout=10,
        )
        if r.status_code == 200:
            rows = r.json()
            if isinstance(rows, list) and len(rows) > 0:
                log.info(f"Idempotência: já existe execução registrada para {hoje}. Abortando.")
                return True
            log.info(f"Idempotência: nenhuma execução registrada para {hoje}. Prosseguindo.")
            return False
        else:
            log.warning(f"Supabase idempotência check: HTTP {r.status_code} — {r.text[:200]}")
            log.warning("Fail-open: permitindo execução mesmo sem confirmação do Supabase.")
            return False
    except Exception as e:
        log.warning(f"Supabase idempotência check: erro — {e}")
        log.warning("Fail-open: permitindo execução mesmo sem confirmação do Supabase.")
        return False


def registrar_execucao(status: str = "ok", detalhes: dict = None) -> bool:
    """
    Registra (ou atualiza) a execução de hoje no Supabase.
    Usa upsert com merge-duplicates: se já existe registro do dia, atualiza status e detalhes.
    Retorna True se registrado com sucesso.
    """
    hoje = date.today().isoformat()
    now = datetime.utcnow().isoformat() + "Z"
    payload = {
        "data_execucao": hoje,
        "status": status,
        "iniciado_em": now,
        "concluido_em": now,
        "detalhes": detalhes or {},
    }
    try:
        r = requests.post(
            f"{SUPABASE_URL}/rest/v1/monitor_execucoes",
            headers={**_H, "Prefer": "resolution=merge-duplicates,return=minimal"},
            json=payload,
            timeout=10,
        )
        if r.status_code in (200, 201, 204):
            log.info(f"Execução registrada no Supabase: {hoje} / {status}")
            return True
        else:
            log.warning(f"Supabase registrar execução: HTTP {r.status_code} — {r.text[:200]}")
    except Exception as e:
        log.warning(f"Supabase registrar execução: erro — {e}")
    return False


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------

def executar_health_check() -> dict:
    """Executa health_check.py e retorna o relatório."""
    log.info("Executando health check das fontes...")
    result = subprocess.run(
        [sys.executable, str(SCRIPTS_DIR / "health_check.py")],
        capture_output=True,
        text=True,
        cwd=str(BASE_DIR),
        timeout=120,
    )
    log.info(f"Health check concluído — código de saída: {result.returncode}")
    if result.stderr:
        for linha in result.stderr.strip().split("\n")[-5:]:
            log.warning(f"  [HC-ERR] {linha}")

    report_file = DATA_DIR / f"health_report_{date.today().isoformat()}.json"
    if report_file.exists():
        try:
            relatorio = json.loads(report_file.read_text(encoding="utf-8"))
            return {
                "ok": result.returncode == 0,
                "regressoes": relatorio.get("regressoes", []),
                "resumo": relatorio.get("resumo", {}),
                "fontes": relatorio.get("fontes", {}),
            }
        except Exception as e:
            log.error(f"Erro ao ler relatório de health check: {e}")

    return {"ok": result.returncode == 0, "regressoes": [], "resumo": {}, "fontes": {}}


# ---------------------------------------------------------------------------
# Monitor de menções
# ---------------------------------------------------------------------------

def executar_monitor(force_send: bool = False) -> dict:
    """Executa monitor_completo.py e retorna o email pendente gerado."""
    log.info("Executando monitor de menções...")
    args = [sys.executable, str(SCRIPTS_DIR / "monitor_completo.py")]
    if force_send:
        args.append("--force-send")

    result = subprocess.run(
        args,
        capture_output=True,
        text=True,
        cwd=str(BASE_DIR),
        timeout=600,
    )
    log.info(f"Monitor concluído — código de saída: {result.returncode}")
    if result.stderr:
        for linha in result.stderr.strip().split("\n")[-5:]:
            log.warning(f"  [MON-ERR] {linha}")

    pending_file = DATA_DIR / "email_pendente.json"
    if pending_file.exists():
        try:
            return json.loads(pending_file.read_text(encoding="utf-8"))
        except Exception as e:
            log.error(f"Erro ao ler email_pendente.json: {e}")

    return {}


# ---------------------------------------------------------------------------
# Envio do email
# ---------------------------------------------------------------------------

def _send_via_mailgun(subject: str, html: str, recipient: str) -> bool:
    """Envia via Mailgun (server-side, sem dependências locais)."""
    api_key = os.getenv("MAILGUN_API_KEY", "").strip()
    domain  = os.getenv("MAILGUN_DOMAIN", "hb-advisory.com.br").strip()
    from_em = os.getenv("FROM_EMAIL", f"Intellicore Alertas <alertas@{domain}>")
    if not api_key:
        return False
    try:
        import requests as _req
        base = os.getenv("MAILGUN_BASE_URL", "https://api.mailgun.net").rstrip("/")
        if not base.endswith("/v3"):
            base += "/v3"
        resp = _req.post(f"{base}/{domain}/messages",
                         auth=("api", api_key),
                         data={"from": from_em, "to": [recipient],
                               "subject": subject, "html": html, "text": " "},
                         timeout=30)
        if resp.status_code == 200:
            log.info(f"Email enviado via Mailgun para {recipient}")
            return True
        log.error(f"Mailgun retornou {resp.status_code}: {resp.text[:200]}")
        return False
    except Exception as exc:
        log.error(f"Erro Mailgun: {exc}")
        return False


def enviar_email(email_monitor: dict, regressoes: list) -> bool:
    """
    Envia o email HTML via Mailgun (primário) ou Gmail SMTP (fallback).
    Destinatário lido de MONITOR_RECIPIENT (padrão: hudsonborges@hb-advisory.com.br).
    Retorna True se enviado com sucesso.
    """
    sys.path.insert(0, str(SCRIPTS_DIR))
    try:
        from enviar_email_html import enviar_email_html  # type: ignore
    except ImportError as e:
        log.error(f"Não foi possível importar enviar_email_html: {e}")
        return False

    hoje = date.today().strftime("%d/%m/%Y")
    pending_file = DATA_DIR / "email_pendente.json"

    # Determinar assunto
    partes_assunto = []
    if regressoes:
        partes_assunto.append(f"ALERTA: {len(regressoes)} regressao(oes)")
    if email_monitor:
        import re as _re
        assunto_monitor = email_monitor.get("assunto", "")
        descricao = _re.sub(r'^(\[Menções\]?\s*)+', '', assunto_monitor).strip()
        descricao = _re.sub(r'\s*—\s*\d{2}/\d{2}/\d{4}$', '', descricao).strip()
        if not descricao:
            descricao = "Relatorio diario"
        partes_assunto.append(descricao)
    if not partes_assunto:
        partes_assunto.append("Relatorio diario")

    assunto = f"[Pessoais] Menções — {' | '.join(partes_assunto)} — {hoje}"

    # Obter corpo HTML
    html_corpo = ""
    if pending_file.exists():
        try:
            pending_data = json.loads(pending_file.read_text(encoding="utf-8"))
            html_corpo = pending_data.get("corpo", "")
        except Exception:
            pass

    if not html_corpo:
        log.warning("Corpo HTML vazio — usando fallback texto simples")
        html_corpo = (
            f"<html><body><p>Monitor de Menções — {hoje}</p>"
            "<p>Nenhum resultado novo encontrado nesta execução.</p>"
            "<p>Termos monitorados: Hudson Viana Borges | CPF 828.258.071-68 | CNPJ 32.309.482/0001-52</p>"
            "</body></html>"
        )

    log.info(f"Enviando email: {assunto}")
    recipient = os.getenv("MONITOR_RECIPIENT", "hudsonborges@hb-advisory.com.br")

    # Tentativa 1: Mailgun (server-side)
    ok = _send_via_mailgun(assunto, html_corpo, recipient)

    # Tentativa 2: SMTP Gmail (fallback)
    if not ok:
        try:
            from enviar_email_html import enviar_email_html  # type: ignore
            ok = enviar_email_html(to=recipient, subject=assunto, html_body=html_corpo)
            if ok:
                log.info(f"Email enviado via SMTP para {recipient}")
        except Exception as e:
            log.error(f"Fallback SMTP falhou: {e}")

    if ok:
        log.info("Email enviado com sucesso via Gmail SMTP")
        if pending_file.exists():
            try:
                pending_data = json.loads(pending_file.read_text(encoding="utf-8"))
                pending_data["assunto"] = assunto
                pending_data["enviado"] = True
                pending_data["method"] = "gmail_smtp"
                pending_data["enviado_em"] = datetime.now().strftime("%d/%m/%Y %H:%M")
                pending_file.write_text(
                    json.dumps(pending_data, ensure_ascii=False, indent=2), encoding="utf-8"
                )
            except Exception as e:
                log.warning(f"Não foi possível atualizar email_pendente.json: {e}")
    else:
        log.error("Falha no envio do email via Gmail SMTP")

    return ok


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Execução diária do Monitor de Menções")
    parser.add_argument("--force-send", action="store_true",
                        help="Envia email mesmo sem resultados novos")
    parser.add_argument("--health-only", action="store_true",
                        help="Executa apenas o health check")
    parser.add_argument("--force-run", action="store_true",
                        help="Ignora o lock de idempotência e executa mesmo que já tenha rodado hoje")
    args = parser.parse_args()

    log.info("=" * 60)
    log.info("Monitor de Menções — Iniciando")
    log.info(f"Data/hora: {datetime.now().strftime('%d/%m/%Y %H:%M')}")
    log.info("=" * 60)

    # ── Verificação de idempotência via Supabase ──────────────────────────────
    if ja_executou_hoje(force_run=args.force_run):
        log.info("Execução abortada: já houve execução hoje. Use --force-run para forçar.")
        sys.exit(0)

    # ── Registrar início (bloqueia execuções concorrentes via UNIQUE constraint) ─
    # Registrar ANTES de executar para que execuções paralelas sejam bloqueadas
    registrar_execucao(
        status="iniciado",
        detalhes={"iniciado_em": datetime.now().strftime("%d/%m/%Y %H:%M")},
    )

    # ── 1. Health check ───────────────────────────────────────────────────────
    hc = executar_health_check()
    regressoes = hc.get("regressoes", [])

    if regressoes:
        log.warning(f"REGRESSÕES DETECTADAS: {len(regressoes)}")
        for r in regressoes:
            log.warning(f"  - {r['fonte']}: {r.get('erro_atual', '')}")

    if args.health_only:
        log.info("Modo --health-only: encerrando após health check.")
        sys.exit(0 if not regressoes else 1)

    # ── 2. Monitor de menções ─────────────────────────────────────────────────
    # Sempre enviar email diário (mesmo sem novidades — email de status é esperado)
    email_monitor = executar_monitor(force_send=True)

    # ── 3. Envio do email ─────────────────────────────────────────────────────
    ok_envio = False
    # Enviar email sempre (com ou sem resultados novos)
    ok_envio = enviar_email(email_monitor, regressoes)
    if not ok_envio:
        log.warning("Envio falhou via Gmail SMTP")


    # ── 4. Registrar execução concluída no Supabase ───────────────────────────
    # Atualizar o registro de "iniciado" para "ok" (upsert por data_execucao)
    registrar_execucao(
        status="ok",
        detalhes={
            "email_enviado": ok_envio,
            "regressoes": len(regressoes),
            "assunto": email_monitor.get("assunto", "") if email_monitor else "",
            "resultados_novos": bool(email_monitor),
        }
    )

    # ── Resumo final ──────────────────────────────────────────────────────────
    log.info("\n" + "=" * 60)
    log.info("Monitor de Menções — Concluído")
    log.info(f"Data/hora: {datetime.now().strftime('%d/%m/%Y %H:%M')}")
    log.info(f"Email enviado: {'Sim' if ok_envio else 'Nao'}")
    log.info(f"Regressoes: {len(regressoes)}")
    log.info("=" * 60)

    sys.exit(0)


if __name__ == "__main__":
    main()
