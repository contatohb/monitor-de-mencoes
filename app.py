#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
app.py — Backend Render do Intellicore Monitor
================================================
Servidor Flask leve que executa o monitor de menções quando acionado.

Arquitetura:
  pg_cron (Supabase) → Edge Function intellicore-monitor → POST /api/internal/run-monitor

Endpoints:
  GET  /health                          — health check (sem autenticação)
  POST /api/internal/run-monitor        — executa run_daily.py (requer Bearer token)
  POST /api/internal/run-monitor-force  — executa com --force-run (requer Bearer token)

Variáveis de ambiente obrigatórias:
  INTERNAL_API_KEY      — token Bearer para autenticação interna
  GMAIL_SMTP_USER       — endereço Gmail (padrão: huddsonviana@gmail.com)
  GMAIL_APP_PASSWORD    — senha de aplicativo Gmail de 16 caracteres
  SUPABASE_URL          — URL do projeto Supabase
  SUPABASE_SERVICE_ROLE_KEY — chave service_role do Supabase
"""

import json
import logging
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from flask import Flask, jsonify, request

# ---------------------------------------------------------------------------
# Configuração
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent
SCRIPTS_DIR = BASE_DIR / "scripts"
LOG_DIR = BASE_DIR / "logs"
LOG_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger(__name__)

INTERNAL_API_KEY = os.environ.get("INTERNAL_API_KEY", "")

app = Flask(__name__)


# ---------------------------------------------------------------------------
# Autenticação
# ---------------------------------------------------------------------------

def _autenticar(req) -> bool:
    """Verifica o token nos cabeçalhos Authorization (Bearer) ou X-Internal-Key."""
    if not INTERNAL_API_KEY:
        log.warning("INTERNAL_API_KEY não configurada — endpoint desprotegido!")
        return True
    # Aceitar Bearer token no Authorization
    auth = req.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        token = auth[len("Bearer "):]
        if token == INTERNAL_API_KEY:
            return True
    # Aceitar X-Internal-Key (enviado pela Edge Function Supabase)
    x_key = req.headers.get("X-Internal-Key", "")
    if x_key == INTERNAL_API_KEY:
        return True
    return False


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.route("/health", methods=["GET"])
def health():
    """Health check público — usado pelo Render para manter o serviço ativo."""
    return jsonify({
        "ok": True,
        "service": "intellicore-monitor",
        "timestamp": datetime.utcnow().isoformat() + "Z",
    })


@app.route("/api/internal/run-monitor", methods=["POST"])
def run_monitor():
    """
    Executa run_daily.py com idempotência via Supabase.
    Se já executou hoje, retorna ok=True sem reexecutar.
    """
    if not _autenticar(request):
        return jsonify({"ok": False, "error": "Unauthorized"}), 401

    return _executar(force_run=False, force_send=False)


@app.route("/api/internal/run-monitor-force", methods=["POST"])
def run_monitor_force():
    """
    Executa run_daily.py ignorando o lock de idempotência.
    Usar apenas para testes ou reexecução manual.
    """
    if not _autenticar(request):
        return jsonify({"ok": False, "error": "Unauthorized"}), 401

    return _executar(force_run=True, force_send=False)


@app.route("/api/internal/run-monitor-force-send", methods=["POST"])
def run_monitor_force_send():
    """
    Executa run_daily.py com --force-send (envia email mesmo sem resultados novos).
    """
    if not _autenticar(request):
        return jsonify({"ok": False, "error": "Unauthorized"}), 401

    return _executar(force_run=True, force_send=True)


# ---------------------------------------------------------------------------
# Execução do monitor
# ---------------------------------------------------------------------------

def _executar(force_run: bool = False, force_send: bool = False):
    """Executa run_daily.py e retorna o resultado como JSON."""
    iniciado_em = datetime.utcnow().isoformat() + "Z"
    log.info(f"Iniciando monitor — force_run={force_run} force_send={force_send}")

    cmd = [sys.executable, str(SCRIPTS_DIR / "run_daily.py")]
    if force_run:
        cmd.append("--force-run")
    if force_send:
        cmd.append("--force-send")

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            cwd=str(BASE_DIR),
            timeout=540,  # 9 minutos (limite Edge Function = 10 min)
        )
    except subprocess.TimeoutExpired:
        log.error("run_daily.py excedeu o timeout de 9 minutos")
        return jsonify({
            "ok": False,
            "error": "Timeout: run_daily.py excedeu 9 minutos",
            "iniciado_em": iniciado_em,
            "concluido_em": datetime.utcnow().isoformat() + "Z",
        }), 504
    except Exception as e:
        log.error(f"Erro ao executar run_daily.py: {e}")
        return jsonify({
            "ok": False,
            "error": str(e),
            "iniciado_em": iniciado_em,
            "concluido_em": datetime.utcnow().isoformat() + "Z",
        }), 500

    concluido_em = datetime.utcnow().isoformat() + "Z"
    ok = result.returncode == 0

    # Ler email_pendente.json se existir
    email_info = {}
    pending_file = BASE_DIR / "data" / "email_pendente.json"
    if pending_file.exists():
        try:
            data = json.loads(pending_file.read_text(encoding="utf-8"))
            email_info = {
                "assunto": data.get("assunto", ""),
                "enviado": data.get("enviado", False),
                "enviado_em": data.get("enviado_em", ""),
            }
        except Exception:
            pass

    log.info(f"Monitor concluído — rc={result.returncode} ok={ok}")

    return jsonify({
        "ok": ok,
        "returncode": result.returncode,
        "iniciado_em": iniciado_em,
        "concluido_em": concluido_em,
        "email": email_info,
        "stdout": result.stdout[-2000:] if result.stdout else "",
        "stderr": result.stderr[-500:] if result.stderr else "",
    })


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    log.info(f"Iniciando Intellicore Monitor Backend na porta {port}")
    app.run(host="0.0.0.0", port=port, debug=False)
