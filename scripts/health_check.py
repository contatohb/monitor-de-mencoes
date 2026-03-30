#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
health_check.py — Projeto Intellicore
Verifica a saúde de todas as fontes monitoradas e detecta regressões
em relação ao estado de referência salvo em data/health_baseline.json.

Critérios de saúde por tipo de fonte:
  - Acesso direto HTTP: resposta 200 (ou 301/302 com redirecionamento válido)
  - API JSON: resposta 200 + campo esperado no JSON
  - DuckDuckGo fallback: resposta 200 do DDG
  - Validação anti-falso-positivo: verificar que a lógica de exclusão está ativa

Saídas:
  - data/health_report_YYYY-MM-DD.json  — relatório detalhado
  - data/health_baseline.json           — estado de referência (atualizado quando tudo OK)
  - Retorna código de saída 0 (tudo OK) ou 1 (há regressões)
"""

import json
import logging
import sys
import time
import urllib3
from datetime import date, datetime
from pathlib import Path

import requests
from bs4 import BeautifulSoup

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ---------------------------------------------------------------------------
# Configuração
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
LOG_DIR = BASE_DIR / "logs"
BASELINE_FILE = DATA_DIR / "health_baseline.json"
REPORT_FILE = DATA_DIR / f"health_report_{date.today().isoformat()}.json"

DATA_DIR.mkdir(exist_ok=True)
LOG_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(
            LOG_DIR / f"health_{date.today().isoformat()}.log", encoding="utf-8"
        ),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

# ---------------------------------------------------------------------------
# Definição das fontes e seus critérios de saúde
# ---------------------------------------------------------------------------

FONTES = [
    # -----------------------------------------------------------------------
    # Diários Oficiais
    # -----------------------------------------------------------------------
    {
        "nome": "DOU",
        "tipo": "http",
        "url": "https://www.in.gov.br/consulta/-/buscar/dou",
        "params": {"q": "teste", "exactDate": "dia"},
        "esperado_status": [200],
        "esperado_texto": None,
        "critico": True,
        "descricao": "Diário Oficial da União (in.gov.br)",
    },
    {
        "nome": "Querido Diário API",
        "tipo": "api_json",
        "url": "https://api.queridodiario.ok.org.br/api/gazettes",
        "params": {"querystring": "teste", "size": 1},
        "campo_json": "gazettes",
        "esperado_status": [200],
        "critico": True,
        "descricao": "API do Querido Diário (OKFN Brasil)",
    },
    # -----------------------------------------------------------------------
    # Bancas — acesso direto
    # -----------------------------------------------------------------------
    {
        "nome": "IADES",
        "tipo": "http",
        "url": "https://www.iades.com.br/inscricao",
        "params": None,
        "esperado_status": [200, 301, 302],
        "critico": False,
        "descricao": "IADES — página de inscrições",
    },
    {
        "nome": "FGV Concursos",
        "tipo": "http",
        "url": "https://conhecimento.fgv.br/concursos",
        "params": None,
        "esperado_status": [200, 301, 302],
        "critico": False,
        "descricao": "FGV Conhecimento — concursos",
    },
    {
        "nome": "CEBRASPE",
        "tipo": "http",
        "url": "https://www.cebraspe.org.br/concursos",
        "params": None,
        "esperado_status": [200, 301, 302],
        "critico": False,
        "descricao": "CEBRASPE — concursos",
    },
    # -----------------------------------------------------------------------
    # Bancas — DuckDuckGo fallback (verificar que DDG responde)
    # -----------------------------------------------------------------------
    {
        "nome": "DDG fallback (VUNESP)",
        "tipo": "ddg",
        "site": "vunesp.com.br",
        "esperado_status": [200],
        "critico": False,
        "descricao": "DuckDuckGo fallback para VUNESP",
    },
    {
        "nome": "DDG fallback (CESGRANRIO)",
        "tipo": "ddg",
        "site": "cesgranrio.org.br",
        "esperado_status": [200],
        "critico": False,
        "descricao": "DuckDuckGo fallback para CESGRANRIO",
    },
    {
        "nome": "DDG fallback (IBFC)",
        "tipo": "ddg",
        "site": "ibfc.org.br",
        "esperado_status": [200],
        "critico": False,
        "descricao": "DuckDuckGo fallback para IBFC",
    },
    {
        "nome": "DDG fallback (QUADRIX)",
        "tipo": "ddg",
        "site": "quadrix.org.br",
        "esperado_status": [200],
        "critico": False,
        "descricao": "DuckDuckGo fallback para QUADRIX",
    },
    # -----------------------------------------------------------------------
    # Editais Culturais — acesso direto
    # -----------------------------------------------------------------------
    {
        "nome": "ProAC/CultSP",
        "tipo": "http",
        "url": "https://www.cultura.sp.gov.br/sec_cultura/Fomento/Portal_do_Fomento",
        "params": None,
        "esperado_status": [200, 301, 302],
        "critico": False,
        "descricao": "ProAC — Secretaria de Cultura SP",
    },
    {
        "nome": "Funarte (gov.br)",
        "tipo": "http",
        "url": "https://www.gov.br/funarte/pt-br/@@search",
        "params": {"SearchableText": "teste"},
        "esperado_status": [200, 301, 302],
        "critico": False,
        "descricao": "Funarte — portal gov.br",
    },
    {
        "nome": "BNDES Cultural",
        "tipo": "http",
        "url": "https://www.bndes.gov.br/wps/portal/site/home/busca",
        "params": {"q": "teste"},
        "esperado_status": [200, 301, 302],
        "critico": False,
        "descricao": "BNDES Cultural",
    },
    {
        "nome": "Caixa Cultural",
        "tipo": "http",
        "url": "https://www.caixa.gov.br/cultura",
        "params": None,
        "esperado_status": [200, 301, 302],
        "critico": False,
        "descricao": "Caixa Cultural",
    },
    {
        "nome": "SMC-SP",
        "tipo": "http",
        "url": "https://www.prefeitura.sp.gov.br/cidade/secretarias/cultura/editais/",
        "params": None,
        "esperado_status": [200, 301, 302],
        "critico": False,
        "descricao": "SMC-SP — editais",
    },
    {
        "nome": "SESC-SP",
        "tipo": "http",
        "url": "https://www.sescsp.org.br/busca/",
        "params": {"q": "teste"},
        "esperado_status": [200, 301, 302],
        "critico": False,
        "descricao": "SESC-SP — busca",
    },
    {
        "nome": "SESI-SP",
        "tipo": "http",
        "url": "https://www.sesisp.org.br/cultura",
        "params": None,
        "esperado_status": [200, 301, 302],
        "critico": False,
        "descricao": "SESI-SP — cultura",
    },
    {
        "nome": "Itaú Cultural",
        "tipo": "http",
        "url": "https://www.itaucultural.org.br/busca",
        "params": {"q": "teste"},
        "esperado_status": [200, 301, 302],
        "critico": False,
        "descricao": "Itaú Cultural — busca",
    },
    {
        "nome": "Santander Cultural",
        "tipo": "http",
        "url": "https://www.santander.com.br/institucional-santander/cultura",
        "params": None,
        "esperado_status": [200, 301, 302],
        "critico": False,
        "descricao": "Santander Cultural",
    },
    {
        "nome": "Instituto Unibanco",
        "tipo": "http",
        "url": "https://www.institutounibanco.org.br/",
        "params": {"s": "teste"},
        "esperado_status": [200, 301, 302],
        "critico": False,
        "descricao": "Instituto Unibanco — busca WordPress",
    },
    # -----------------------------------------------------------------------
    # Editais Culturais — DuckDuckGo fallback
    # -----------------------------------------------------------------------
    {
        "nome": "DDG fallback (CCBB)",
        "tipo": "ddg",
        "site": "culturabancodobrasil.com.br",
        "esperado_status": [200],
        "critico": False,
        "descricao": "DuckDuckGo fallback para CCBB",
    },
    {
        "nome": "DDG fallback (Petrobras Cultural)",
        "tipo": "ddg",
        "site": "petrobras.com.br",
        "esperado_status": [200],
        "critico": False,
        "descricao": "DuckDuckGo fallback para Petrobras Cultural",
    },
    {
        "nome": "DDG fallback (Natura Musical)",
        "tipo": "ddg",
        "site": "naturamusical.com.br",
        "esperado_status": [200],
        "critico": False,
        "descricao": "DuckDuckGo fallback para Natura Musical",
    },
    {
        "nome": "DDG fallback (Vale Cultural)",
        "tipo": "ddg",
        "site": "vale.com",
        "esperado_status": [200],
        "critico": False,
        "descricao": "DuckDuckGo fallback para Vale Cultural",
    },
    # -----------------------------------------------------------------------
    # Validação da lógica anti-falso-positivo (teste sintético)
    # -----------------------------------------------------------------------
    {
        "nome": "Validacao anti-falso-positivo",
        "tipo": "logica",
        "critico": True,
        "descricao": "Verifica que a funcao _validar_resultado_real rejeita falsos positivos conhecidos",
    },
]

# ---------------------------------------------------------------------------
# Verificadores por tipo
# ---------------------------------------------------------------------------

def checar_http(fonte: dict) -> dict:
    url = fonte["url"]
    params = fonte.get("params")
    esperados = fonte.get("esperado_status", [200])
    try:
        r = requests.get(
            url, params=params, headers=HEADERS,
            timeout=20, verify=False, allow_redirects=True,
        )
        ok = r.status_code in esperados
        return {
            "status_code": r.status_code,
            "ok": ok,
            "url_final": r.url,
            "erro": None if ok else f"HTTP {r.status_code} nao esperado (esperados: {esperados})",
        }
    except Exception as e:
        return {"status_code": None, "ok": False, "url_final": url, "erro": str(e)}


def checar_api_json(fonte: dict) -> dict:
    url = fonte["url"]
    params = fonte.get("params")
    campo = fonte.get("campo_json", "")
    try:
        r = requests.get(
            url, params=params,
            headers={**HEADERS, "Accept": "application/json"},
            timeout=20, verify=False,
        )
        if r.status_code not in fonte.get("esperado_status", [200]):
            return {
                "status_code": r.status_code,
                "ok": False,
                "erro": f"HTTP {r.status_code}",
            }
        data = r.json()
        campo_presente = campo in data if campo else True
        return {
            "status_code": r.status_code,
            "ok": campo_presente,
            "campo_json": campo,
            "campo_presente": campo_presente,
            "erro": None if campo_presente else f"Campo '{campo}' ausente no JSON",
        }
    except Exception as e:
        return {"status_code": None, "ok": False, "erro": str(e)}


def checar_ddg(fonte: dict) -> dict:
    """
    Verifica que o DuckDuckGo responde para a busca site:dominio.
    Aceita HTTP 200 e 202 como respostas validas:
    - 200: resposta normal com resultados HTML
    - 202: resposta de rate limiting temporario do DDG (servico ativo mas limitando)
    Apenas 4xx (exceto 429) e 5xx indicam falha real.
    """
    site = fonte["site"]
    try:
        r = requests.get(
            "https://html.duckduckgo.com/html/",
            params={"q": f"site:{site}"},
            headers=HEADERS,
            timeout=15,
            verify=False,
        )
        # 200 = OK normal; 202 = rate limit temporario (DDG ativo)
        ok = r.status_code in [200, 202]
        return {
            "status_code": r.status_code,
            "ok": ok,
            "nota": "HTTP 202 = rate limit temporario do DDG (servico ativo)" if r.status_code == 202 else None,
            "erro": None if ok else f"DDG retornou HTTP {r.status_code} (falha real)",
        }
    except Exception as e:
        return {"status_code": None, "ok": False, "erro": str(e)}


def checar_logica_antifp() -> dict:
    """
    Testa a função _validar_resultado_real importada do monitor_completo.py
    com casos de falso positivo conhecidos (Instituto Unibanco, WordPress genérico).
    """
    try:
        import importlib.util, os
        spec = importlib.util.spec_from_file_location(
            "monitor_completo",
            BASE_DIR / "scripts" / "monitor_completo.py",
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        fn = mod._validar_resultado_real

        # Caso 1: falso positivo WordPress ("você pesquisou por X")
        texto_fp1 = "Você pesquisou por Hudson Viana Borges - Instituto Unibanco resultado de busca você buscou por: Hudson Viana Borges EDUCACAO EM PAUTA"
        resultado_fp1 = fn(texto_fp1, "Hudson Viana Borges")
        # Deve retornar False (falso positivo rejeitado)

        # Caso 2: resultado real (nome aparece em contexto de conteúdo)
        texto_real = "O pesquisador Hudson Viana Borges foi selecionado para o programa de bolsas 2025 conforme edital publicado em 10/03/2025."
        resultado_real = fn(texto_real, "Hudson Viana Borges")
        # Deve retornar True

        # Caso 3: texto sem o termo
        texto_vazio = "Nenhuma informação relevante encontrada nesta página."
        resultado_vazio = fn(texto_vazio, "Hudson Viana Borges")
        # Deve retornar False

        ok = (resultado_fp1 is False) and (resultado_real is True) and (resultado_vazio is False)
        detalhes = {
            "caso1_fp_wordpress_rejeitado": resultado_fp1 is False,
            "caso2_resultado_real_aceito": resultado_real is True,
            "caso3_sem_termo_rejeitado": resultado_vazio is False,
        }
        return {
            "ok": ok,
            "detalhes": detalhes,
            "erro": None if ok else f"Falha nos casos: {[k for k,v in detalhes.items() if not v]}",
        }
    except Exception as e:
        return {"ok": False, "erro": f"Erro ao importar monitor_completo: {e}"}


# ---------------------------------------------------------------------------
# Carregamento e comparação com baseline
# ---------------------------------------------------------------------------

def carregar_baseline() -> dict:
    if BASELINE_FILE.exists():
        try:
            return json.loads(BASELINE_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def salvar_baseline(resultados: dict) -> None:
    BASELINE_FILE.write_text(
        json.dumps(resultados, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def detectar_regressoes(atual: dict, baseline: dict) -> list[dict]:
    """
    Compara o estado atual com o baseline.
    Uma regressão ocorre quando uma fonte que estava OK no baseline
    passou a falhar no estado atual.
    """
    regressoes = []
    for nome, estado_atual in atual.items():
        estado_base = baseline.get(nome, {})
        estava_ok = estado_base.get("ok", None)
        esta_ok = estado_atual.get("ok", False)
        if estava_ok is True and esta_ok is False:
            regressoes.append({
                "fonte": nome,
                "era_ok": True,
                "agora_ok": False,
                "erro_atual": estado_atual.get("erro", "desconhecido"),
                "status_atual": estado_atual.get("status_code"),
            })
    return regressoes


# ---------------------------------------------------------------------------
# Execução principal
# ---------------------------------------------------------------------------

def main() -> int:
    log.info("=" * 60)
    log.info("Intellicore Health Check — Iniciando")
    log.info(f"Data/hora: {datetime.now().isoformat()}")
    log.info("=" * 60)

    baseline = carregar_baseline()
    resultados = {}

    for fonte in FONTES:
        nome = fonte["nome"]
        tipo = fonte["tipo"]
        log.info(f"Verificando: {nome} ({tipo})")

        if tipo == "http":
            r = checar_http(fonte)
        elif tipo == "api_json":
            r = checar_api_json(fonte)
        elif tipo == "ddg":
            r = checar_ddg(fonte)
        elif tipo == "logica":
            r = checar_logica_antifp()
        else:
            r = {"ok": False, "erro": f"Tipo desconhecido: {tipo}"}

        r["nome"] = nome
        r["tipo"] = tipo
        r["critico"] = fonte.get("critico", False)
        r["descricao"] = fonte.get("descricao", "")
        r["timestamp"] = datetime.now().isoformat()

        status_str = "OK" if r["ok"] else "FALHA"
        log.info(f"  [{status_str}] {nome}: {r.get('erro') or 'sem erros'}")
        resultados[nome] = r
        time.sleep(0.5)

    # Detectar regressões
    regressoes = detectar_regressoes(resultados, baseline)

    # Resumo
    total = len(resultados)
    ok_count = sum(1 for r in resultados.values() if r["ok"])
    falha_count = total - ok_count
    criticos_falhando = [
        r for r in resultados.values() if not r["ok"] and r.get("critico")
    ]

    log.info("=" * 60)
    log.info(f"Resultado: {ok_count}/{total} fontes OK, {falha_count} com falha")
    log.info(f"Regressoes detectadas: {len(regressoes)}")
    log.info(f"Fontes criticas falhando: {len(criticos_falhando)}")
    log.info("=" * 60)

    # Salvar relatório
    relatorio = {
        "timestamp": datetime.now().isoformat(),
        "resumo": {
            "total": total,
            "ok": ok_count,
            "falha": falha_count,
            "regressoes": len(regressoes),
            "criticos_falhando": len(criticos_falhando),
        },
        "regressoes": regressoes,
        "fontes": resultados,
    }
    REPORT_FILE.write_text(
        json.dumps(relatorio, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    log.info(f"Relatorio salvo em: {REPORT_FILE}")

    # Atualizar baseline apenas se não houver regressões e tudo OK
    if not regressoes and falha_count == 0:
        salvar_baseline(resultados)
        log.info("Baseline atualizado (todas as fontes OK).")
    elif not regressoes:
        # Há falhas mas não são regressões (eram falhas conhecidas no baseline)
        log.info("Baseline mantido (falhas conhecidas, sem novas regressoes).")
    else:
        log.warning(f"Baseline NAO atualizado — {len(regressoes)} regressao(oes) detectada(s).")

    # Código de saída: 0 = OK, 1 = há regressões ou críticos falhando
    tem_problema = bool(regressoes) or bool(criticos_falhando)
    return 1 if tem_problema else 0


if __name__ == "__main__":
    sys.exit(main())
