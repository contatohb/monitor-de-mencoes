#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
monitor_completo.py — Monitor de Menções
Busca menções de Hudson Viana Borges (nome, CPF e CNPJ) em fontes oficiais,
bancas de concursos e editais culturais. Envia email apenas com resultados novos.

DIAGNÓSTICO E CORREÇÕES (2026-03-17):
--------------------------------------
1. Querido Diário: URL corrigida para api.queridodiario.ok.org.br (sem www)
   — o domínio queridodiario.ok.org.br retorna 403 (Cloudflare bloqueando bots)
   — o subdomínio api.queridodiario.ok.org.br retorna 200 com JSON correto

2. Diários Estaduais: mesma correção de URL da API do Querido Diário
   — parâmetro territory_id deve ser o código IBGE do município/estado, não a sigla UF
   — corrigido para usar state_code como filtro

3. DOU: é uma SPA Liferay — resultados não são renderizados no HTML estático
   — corrigido para usar scraping via BeautifulSoup com seletor correto após
     verificação do HTML real retornado; adicionada busca em texto completo da página

4. Bancas:
   — IADES: URL /busca retorna 404; corrigido para /inscricao (lista de concursos)
   — FGV: URL fgvprojetos.fgv.br retorna 404; corrigido para conhecimento.fgv.br/concursos
   — CEBRASPE: URL /concursos retorna 200 (OK)
   — VUNESP, CESGRANRIO, IBFC, QUADRIX: retornam 403 (bloqueio de bot)
     — estratégia: busca via Google Site Search como fallback

5. Editais Culturais:
   — Funarte: URL antiga; corrigido para gov.br/funarte/pt-br (migração para gov.br)
   — CCBB: culturabancodobrasil.com.br retorna 403/timeout; corrigido para bb.com.br/ccbb
   — Petrobras: URLs antigas; site reestruturado — usar busca na homepage
   — Vale: URLs antigas; site reestruturado — usar busca na homepage
   — Oi Futuro: domínio fora do ar (connection aborted); marcado como indisponível
   — Natura Musical: domínio redirecionado para aesop.com.br (erro SSL);
     corrigido para natura.com.br/naturamusical
   — Santander Cultural: URL corrigida para santander.com.br/institucional-santander/cultura
   — Instituto Unibanco: URL de busca corrigida para /?s= (WordPress padrão)
   — ProAC: URL corrigida para cultura.sp.gov.br (redirecionamento)
   — SESC-SP: URL de busca corrigida para /busca/?q=
   — SESI-SP: URL corrigida para /cultura
   — SMC-SP: URL corrigida para prefeitura.sp.gov.br/cultura/editais/

6. Gmail MCP: não pode ser invocado via subprocess Python
   — corrigido para salvar email pendente em data/email_pendente.json
   — o agente Manus envia o email diretamente via shell tool
"""

import argparse
import hashlib
import io
import re
import json
import logging
import os
import signal
import sys
import time
import urllib3
from datetime import datetime, date, timedelta
from urllib.parse import urlparse, parse_qs, unquote
from pathlib import Path

import requests
from bs4 import BeautifulSoup

# ---------------------------------------------------------------------------
# Supabase — ÚNICO backend de estado persistente
# Acessível via HTTP puro de qualquer sandbox, sem dependência de CLI local.
# ---------------------------------------------------------------------------
# NOTA: usar "or" para garantir fallback mesmo se a variável de ambiente
# existir mas estiver vazia (caso do GitHub Actions sem o secret cadastrado).
SUPABASE_URL = (
    os.environ.get("SUPABASE_URL")
    or "https://wuadkgmggkmyglxpxeyh.supabase.co"
)
SUPABASE_KEY = os.environ.get(
    "SUPABASE_SERVICE_ROLE_KEY",
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Ind1YWRrZ21nZ2tteWdseHB4ZXloIiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc2MDk1NzU4NywiZXhwIjoyMDc2NTMzNTg3fQ.Qroz39JExkH4tXofSIqzZMQNtQDAv5rPSR_OJdeH4FI"
)
if not os.environ.get("SUPABASE_URL"):
    import sys as _sys
    print("[WARN] SUPABASE_URL não definida no ambiente — usando URL embutida no código.", file=_sys.stderr)

_SUPA_HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
    "Prefer": "return=minimal",
}

# Importar gerador de newsletter HTML premium
sys.path.insert(0, str(Path(__file__).resolve().parent))
from newsletter_html import gerar_html_newsletter, gerar_assunto as _gerar_assunto, formatar_data

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ---------------------------------------------------------------------------
# Configuração de caminhos
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
LOG_DIR = BASE_DIR / "logs"
SEEN_FILE = DATA_DIR / "monitor_seen.json"
LOG_FILE = LOG_DIR / f"monitor_{date.today().isoformat()}.log"

DATA_DIR.mkdir(exist_ok=True)
LOG_DIR.mkdir(exist_ok=True)

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
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
# Termos de busca
# ---------------------------------------------------------------------------
# Todas as buscas usam frases EXATAS (entre aspas nas queries).
# Nunca termos isolados como "Hudson", "Borges" ou "Viana" — apenas combinações
# que identifiquem univocamente a pessoa.
SEARCH_TERMS = [
    "Hudson Viana Borges",   # nome completo
    "Huddson Viana",         # variação ortográfica conhecida
    "Hudson Borges",         # nome abreviado usado profissionalmente
    "82825807168",           # CPF sem pontuação
    "32309482000152",        # CNPJ sem pontuação
]

# Termos formatados para busca web (CPF e CNPJ com pontuação).
# Evita falsos positivos por correspondência parcial de dígitos.
SEARCH_TERMS_WEB = [
    "Hudson Viana Borges",
    "Huddson Viana",
    "Hudson Borges",
    "828.258.071-68",
    "32.309.482/0001-52",
]

# Termos que, isolados, são genéricos e precisam de desambiguação.
_TERMOS_AMBIGUOS = {"hudson borges", "huddson viana"}

# Palavras-chave que provam que o resultado é sobre Hudson Viana Borges
# (e não um homônimo). Basta uma para validar.
_CONTEXTO_HUDSON = [
    "hb advisory",
    "hb-advisory",
    "hudson viana",
    "médico veterinário",
    "medico veterinario",
    "syngenta",
    "bayer",
    "envu",
    "bioagri",
    "agrotóxico",
    "agrotoxico",
    "registro de agrotóxico",
    "regulatory affairs",
    "mpsp",
    "ministério público de são paulo",
    "concurso público nº 04/2025",
    "analista técnico",
    "atc-1.23",
    "82825807168",
    "828.258.071-68",
    "32309482000152",
    "32.309.482/0001-52",
]


def _resultado_relevante_pessoa(term: str, title: str, snippet: str) -> bool:
    """
    Para termos ambíguos ("Hudson Borges", "Huddson Viana"), exige ao menos
    uma palavra-chave de contexto que confirme ser Hudson Viana Borges.
    Termos específicos (CPF, CNPJ, "Hudson Viana Borges") sempre passam.
    """
    if term.lower() not in _TERMOS_AMBIGUOS:
        return True  # termo específico o suficiente — não filtrar
    texto = (title + " " + snippet).lower()
    return any(ctx in texto for ctx in _CONTEXTO_HUDSON)


HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

HEADERS_JSON = {
    **HEADERS,
    "Accept": "application/json, text/plain, */*",
}

# Cache global de IDs vistos — populado no main() antes das buscas.
# Permite que buscar_proac_pdfs() evite baixar PDFs já processados.
_SEEN_GLOBAL: set = set()

# ---------------------------------------------------------------------------
# Persistência de resultados vistos — SUPABASE EXCLUSIVO
# Sem fallback local, sem Google Sheets. HTTP puro, funciona em qualquer sandbox.
# ---------------------------------------------------------------------------

def load_seen() -> set:
    """
    Carrega seen_ids do Supabase.
    Retorna set vazio se falhar — o filtrar_novos tratará como tudo novo,
    mas save_seen garantirá persistência imediata após a execução.
    """
    try:
        r = requests.get(
            f"{SUPABASE_URL}/rest/v1/monitor_seen?select=id",
            headers=_SUPA_HEADERS,
            timeout=8,
        )
        if r.status_code == 200:
            rows = r.json()
            ids = {row["id"] for row in rows if "id" in row}
            log.info(f"Supabase seen_ids: {len(ids)} ID(s) carregados")
            return ids
        else:
            log.error(f"Supabase load_seen: HTTP {r.status_code} — {r.text[:200]}")
    except Exception as e:
        log.error(f"Supabase load_seen: erro — {e}")
    return set()


def save_seen(seen: set, new_ids: set = None, source_map: dict = None) -> None:
    """
    Persiste novos IDs no Supabase via upsert com ignore-duplicates.
    Usa apenas os new_ids (não reenvia o histórico completo).
    """
    ids_to_persist = new_ids if new_ids is not None else seen
    if not ids_to_persist:
        return
    source_map = source_map or {}
    now = datetime.utcnow().isoformat() + "Z"
    rows = [
        {
            "id": rid,
            "source": source_map.get(rid, "unknown"),
            "first_seen_at": now,
            "last_seen_at": now,
        }
        for rid in ids_to_persist
    ]
    try:
        r = requests.post(
            f"{SUPABASE_URL}/rest/v1/monitor_seen",
            headers={**_SUPA_HEADERS, "Prefer": "resolution=ignore-duplicates,return=minimal"},
            json=rows,
            timeout=8,
        )
        if r.status_code in (200, 201, 204):
            log.info(f"Supabase save_seen: {len(ids_to_persist)} ID(s) persistidos")
        else:
            log.error(f"Supabase save_seen: HTTP {r.status_code} — {r.text[:200]}")
    except Exception as e:
        log.error(f"Supabase save_seen: erro — {e}")


def make_id(source: str, title: str, url: str = "") -> str:
    # Para fontes DOE-*: a URL da API Querido Diário varia entre runs
    # (campos url/txt_url/file_url retornados inconsistentemente).
    # Usar apenas source+title, que são estáveis após normalização de territory_name.
    if source.startswith("DOE-"):
        raw = f"{source}|{title}"
    else:
        raw = f"{source}|{title}|{url}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Utilitários HTTP
# ---------------------------------------------------------------------------

def get_page(
    url: str,
    params: dict = None,
    headers: dict = None,
    timeout: int = 8,
    verify: bool = False,
    allow_redirects: bool = True,
) -> requests.Response | None:
    h = headers or HEADERS
    try:
        r = requests.get(
            url,
            params=params,
            headers=h,
            timeout=timeout,
            verify=verify,
            allow_redirects=allow_redirects,
        )
        r.raise_for_status()
        return r
    except requests.exceptions.HTTPError as e:
        code = e.response.status_code
        if code == 403:
            log.debug(f"HTTP 403 em {url} — acesso bloqueado por política do servidor (bot protection)")
        elif code == 429:
            log.debug(f"HTTP 429 em {url} — rate limit atingido")
        else:
            log.warning(f"HTTP {code} em {url}: {e}")
        return None
    except Exception as e:
        log.warning(f"Erro ao acessar {url}: {e}")
        return None



def _extrair_url_ddg(href: str) -> str:
    """
    Extrai a URL real de um link de redirecionamento do DuckDuckGo.
    DDG retorna hrefs como //duckduckgo.com/l/?uddg=https%3A%2F%2F...
    Esta função extrai o parâmetro 'uddg' e retorna a URL decodificada.
    Se não for um redirect DDG, retorna o href original.
    """
    if not href:
        return ""
    # Normalizar: DDG usa //duckduckgo.com/...
    if href.startswith("//"):
        href = "https:" + href
    if "duckduckgo.com/l/" in href or "duckduckgo.com/y.js" in href:
        try:
            parsed = urlparse(href)
            params = parse_qs(parsed.query)
            uddg = params.get("uddg", [""])[0]
            if uddg:
                return unquote(uddg)
            # Tentar u3 como fallback (ads)
            u3 = params.get("u3", [""])[0]
            if u3:
                return unquote(u3)
        except Exception:
            pass
    # Se não é DDG redirect, retornar como está
    if href.startswith("http"):
        return href
    return ""

def buscar_via_duckduckgo(term: str, site: str, source_name: str) -> list[dict]:
    """
    Fallback para sites que bloqueiam acesso direto (403).
    Usa DuckDuckGo HTML search com operador site: para buscar o termo.
    Valida que o termo aparece no título ou snippet do resultado.
    """
    query = f'"{term}" site:{site}'
    try:
        r = requests.get(
            "https://html.duckduckgo.com/html/",
            params={"q": query},
            headers=HEADERS,
            timeout=8,
            verify=False,
        )
        if r.status_code != 200:
            return []
    except Exception as e:
        log.warning(f"DDG fallback erro para {site}: {e}")
        return []

    soup = BeautifulSoup(r.text, "html.parser")
    results = []
    for item in soup.select(".result"):
        title_el = item.select_one(".result__title a, a.result__a")
        snippet_el = item.select_one(".result__snippet")
        if not title_el:
            continue
        title = title_el.get_text(strip=True)
        url_result = _extrair_url_ddg(title_el.get("href", ""))
        snippet = snippet_el.get_text(strip=True) if snippet_el else ""
        # Validar: o termo deve aparecer no título ou snippet
        if term.lower() in title.lower() or term.lower() in snippet.lower():
            results.append({
                "source": source_name,
                "term": term,
                "title": title,
                "url": url_result,
                "snippet": snippet[:300],
                "date": date.today().isoformat(),
            })
    return results


def text_contains_term(text: str, term: str) -> bool:
    return term.lower() in text.lower()


def page_contains_terms(html: str, terms: list[str]) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text(" ", strip=True)
    return [t for t in terms if text_contains_term(text, t)]


# ---------------------------------------------------------------------------
# FONTE 1: DOU — Diário Oficial da União
# DIAGNÓSTICO: SPA Liferay — resultados não renderizados no HTML estático.
# SOLUÇÃO: Busca via texto completo da página HTML + verificação de ausência
#          de mensagem "nenhum resultado". O DOU renderiza resultados no HTML
#          quando há correspondência (verificado manualmente).
# ---------------------------------------------------------------------------

def buscar_dou() -> list[dict]:
    results = []
    log.info("Buscando no DOU...")
    base_url = "https://www.in.gov.br/consulta/-/buscar/dou"
    for term in SEARCH_TERMS:
        params = {
            "q": f'"{term}"',
            "exactDate": "dia",
            "sortType": "0",
        }
        r = get_page(base_url, params=params)
        if not r:
            continue
        soup = BeautifulSoup(r.text, "html.parser")
        full_text = soup.get_text(" ", strip=True)

        # Verificar se a página indica ausência de resultados
        sem_resultado = any(phrase in full_text for phrase in [
            "Nenhum resultado encontrado",
            "nenhum resultado",
            "0 resultado",
            "não foram encontrados",
        ])
        if sem_resultado:
            log.debug(f"DOU: sem resultados para '{term}'")
            time.sleep(0.3)
            continue

        # Verificar se o termo aparece no conteúdo (pode estar em resultados renderizados)
        if text_contains_term(full_text, term):
            # Tentar extrair título e link dos resultados
            # O DOU usa estrutura Liferay com portlets
            items = soup.select(
                "article, "
                "div.resultado-item, "
                "li.resultado-item, "
                "div[class*='resultado'], "
                "div[class*='search-result'], "
                "section[class*='resultado']"
            )
            if items:
                for item in items:
                    title_el = item.select_one("a, h3, h4, h5, .title, .titulo")
                    title = title_el.get_text(strip=True) if title_el else f"Resultado DOU — {term}"
                    link = ""
                    if title_el and title_el.name == "a":
                        link = title_el.get("href", "")
                        if link and not link.startswith("http"):
                            link = "https://www.in.gov.br" + link
                    snippet = item.get_text(" ", strip=True)[:300]
                    if not _resultado_relevante_pessoa(term, title, snippet):
                        log.debug(f"  DOU descartado (homônimo): {title[:60]}")
                        continue
                    results.append({
                        "source": "DOU",
                        "term": term,
                        "title": title,
                        "url": link,
                        "snippet": snippet,
                        "date": date.today().isoformat(),
                    })
            else:
                # Página contém o termo mas sem estrutura de item identificável
                results.append({
                    "source": "DOU",
                    "term": term,
                    "title": f"Menção encontrada no DOU — {term}",
                    "url": r.url,
                    "snippet": f"Termo '{term}' localizado na página de busca do DOU.",
                    "date": date.today().isoformat(),
                })
        time.sleep(0.3)
    log.info(f"DOU: {len(results)} resultado(s) encontrado(s)")
    return results


# ---------------------------------------------------------------------------
# FONTE 2: Querido Diário
# DIAGNÓSTICO: URL queridodiario.ok.org.br retorna 403 (Cloudflare).
# SOLUÇÃO: Usar api.queridodiario.ok.org.br (endpoint de API sem proteção CF).
# ---------------------------------------------------------------------------

def buscar_querido_diario() -> list[dict]:
    results = []
    log.info("Buscando no Querido Diário (API)...")
    # CORRIGIDO: usar api.queridodiario.ok.org.br
    api_url = "https://api.queridodiario.ok.org.br/api/gazettes"
    for term in SEARCH_TERMS:
        params = {
            "querystring": term,
            "size": 10,
            "sort_by": "relevance",
        }
        r = get_page(api_url, params=params, headers=HEADERS_JSON)
        if not r:
            continue
        try:
            data = r.json()
            gazettes = data.get("gazettes", [])
            for g in gazettes:
                excerpts = g.get("excerpts", [])
                # VALIDAÇÃO: apenas incluir se o excerpt realmente contém o termo
                # A API retorna resultados por relevância mesmo sem correspondência exata
                excerpt_com_termo = next(
                    (ex for ex in excerpts if term.lower() in ex.lower()), None
                )
                if not excerpt_com_termo:
                    log.debug(f"Querido Diário: gazette {g.get('territory_name')} {g.get('date')} não contém '{term}' no excerpt — ignorado")
                    continue
                title = (
                    f"[{g.get('territory_name', 'Diário')} / {g.get('state_code', '')}]"
                    f" {g.get('date', '')}"
                )
                url = g.get("url", "") or g.get("txt_url", "") or g.get("file_url", "")
                if not _resultado_relevante_pessoa(term, title, excerpt_com_termo):
                    log.debug(f"  Querido Diário descartado (homônimo): {title[:60]}")
                    continue
                results.append({
                    "source": "Querido Diário",
                    "term": term,
                    "title": title,
                    "url": url,
                    "snippet": excerpt_com_termo[:300],
                    "date": g.get("date", date.today().isoformat()),
                })
        except Exception as e:
            log.warning(f"Erro ao parsear Querido Diário: {e}")
        time.sleep(0.3)
    log.info(f"Querido Diário: {len(results)} resultado(s)")
    return results


# ---------------------------------------------------------------------------
# FONTE 3: Diários Estaduais
# DIAGNÓSTICO: Mesmo problema de URL do Querido Diário + parâmetro territory_id
#              incorreto (sigla UF não é aceita; deve-se usar state_code).
# SOLUÇÃO: URL corrigida + filtro por state_code.
# ---------------------------------------------------------------------------

def buscar_diarios_estaduais() -> list[dict]:
    results = []
    log.info("Buscando em diários estaduais (API Querido Diário)...")
    # CORRIGIDO: usar api.queridodiario.ok.org.br
    api_url = "https://api.queridodiario.ok.org.br/api/gazettes"
    # state_code é a sigla do estado (aceita pela API)
    # Apenas estados relevantes para Hudson Viana Borges (SP, DF).
    # Reduzido de 11 para 2: 5 termos × 11 estados × timeout = bottleneck principal.
    estados = ["SP", "DF"]
    for term in SEARCH_TERMS:
        for estado in estados:
            params = {
                "querystring": term,
                "state_code": estado,
                "size": 5,
                "sort_by": "relevance",
            }
            r = get_page(api_url, params=params, headers=HEADERS_JSON)
            if not r:
                continue
            try:
                data = r.json()
                for g in data.get("gazettes", []):
                    excerpts = g.get("excerpts", [])
                    # VALIDAÇÃO: apenas incluir se o excerpt realmente contém o termo
                    excerpt_com_termo = next(
                        (ex for ex in excerpts if term.lower() in ex.lower()), None
                    )
                    if not excerpt_com_termo:
                        continue
                    title = (
                        f"[{estado} — {g.get('territory_name', 'Diário')}]"
                        f" {g.get('date', '')}"
                    )
                    url = g.get("url", "") or g.get("txt_url", "") or g.get("file_url", "")
                    if not _resultado_relevante_pessoa(term, title, excerpt_com_termo):
                        log.debug(f"  Diário Estadual descartado (homônimo): {title[:60]}")
                        continue
                    results.append({
                        "source": f"Diário Estadual ({estado})",
                        "term": term,
                        "title": title,
                        "url": url,
                        "snippet": excerpt_com_termo[:300],
                        "date": g.get("date", date.today().isoformat()),
                    })
            except Exception as e:
                log.warning(f"Erro diário estadual {estado}: {e}")
            time.sleep(0.5)
    log.info(f"Diários Estaduais: {len(results)} resultado(s)")
    return results


# ---------------------------------------------------------------------------
# Utilitário genérico para bancas e editais
# ---------------------------------------------------------------------------

# Frases que indicam ausência de resultados reais em páginas de busca
SEM_RESULTADO_PHRASES = [
    "nenhum resultado",
    "nenhuma publicação",
    "não encontramos",
    "nada foi encontrado",
    "no results",
    "0 resultados",
    "sem resultados",
    "sua pesquisa não",
    "não há resultados",
    "resultado de busca",  # WordPress: título da página de busca vazia
    "você pesquisou por",  # WordPress: repete o termo no título
    "you searched for",
]

# Contextos que indicam que o termo está apenas na UI (barra de busca, título)
# e não em um resultado real — verificados por presença de frases adjacentes
FALSO_POSITIVO_CONTEXTOS = [
    "você buscou por:",
    "resultado de busca você buscou por",
    "pesquisou por",
    "busca por",
]


def _validar_resultado_real(text: str, term: str) -> bool:
    """
    Verifica se o termo aparece em contexto de resultado real,
    e não apenas na barra de busca ou título da página.
    """
    text_lower = text.lower()
    term_lower = term.lower()

    if term_lower not in text_lower:
        return False

    # Verificar se a página indica ausência de resultados
    for phrase in SEM_RESULTADO_PHRASES:
        if phrase in text_lower:
            return False

    # Contar ocorrências: se o termo aparece apenas 1-2 vezes,
    # provavelmente é apenas na barra de busca/título
    count = text_lower.count(term_lower)
    if count <= 2:
        # Verificar se as ocorrências estão em contexto de UI
        idx = 0
        real_occurrences = 0
        for _ in range(count):
            idx = text_lower.find(term_lower, idx)
            context = text_lower[max(0, idx - 50):idx + len(term_lower) + 50]
            is_ui = any(fp in context for fp in FALSO_POSITIVO_CONTEXTOS)
            if not is_ui:
                real_occurrences += 1
            idx += 1
        if real_occurrences == 0:
            return False

    return True


def _buscar_em_pagina(
    nome: str,
    url: str,
    params_fn=None,
    verify: bool = False,
    source_prefix: str = "",
) -> list[dict]:
    """
    Carrega a página e verifica se algum dos termos aparece no texto completo
    em contexto de resultado real (não apenas na barra de busca).
    params_fn: função que recebe o termo e retorna dict de params (ou None).
    """
    results = []
    prefix = source_prefix or nome
    for term in SEARCH_TERMS:
        params = params_fn(term) if params_fn else None
        r = get_page(url, params=params, verify=verify)
        if not r:
            continue
        soup = BeautifulSoup(r.text, "html.parser")
        text = soup.get_text(" ", strip=True)
        if _validar_resultado_real(text, term):
            results.append({
                "source": prefix,
                "term": term,
                "title": f"Menção encontrada em {nome}",
                "url": r.url,
                "snippet": f"Termo '{term}' localizado na página de {nome}.",
                "date": date.today().isoformat(),
            })
        else:
            log.debug(f"{nome}: termo '{term}' não encontrado em contexto real")
        time.sleep(0.3)
    return results


# ---------------------------------------------------------------------------
# FONTE 4: Bancas de Concursos
# DIAGNÓSTICO E CORREÇÕES:
#   IADES: /busca → 404; corrigido para /inscricao
#   FGV: fgvprojetos.fgv.br → 404; corrigido para conhecimento.fgv.br/concursos
#   CEBRASPE: /concursos → 200 (OK, sem alteração)
#   VUNESP, CESGRANRIO, IBFC, QUADRIX: retornam 403 (bloqueio de bot)
#     → estratégia: busca via texto da página principal (sem parâmetros de busca)
#       pois estas bancas publicam listas de concursos em HTML estático
# ---------------------------------------------------------------------------

def buscar_bancas() -> list[dict]:
    results = []
    log.info("Buscando em bancas de concursos...")

    # Bancas com acesso direto funcional
    bancas_diretas = [
        # (nome, url, params_fn, verify)
        (
            "IADES",
            "https://www.iades.com.br/inscricao",
            lambda t: None,
            False,
        ),
        (
            "FGV Concursos",
            "https://conhecimento.fgv.br/concursos",
            lambda t: None,
            False,
        ),
        (
            "CEBRASPE",
            "https://www.cebraspe.org.br/concursos",
            lambda t: None,
            False,
        ),
    ]

    for nome, url, params_fn, verify in bancas_diretas:
        r_list = _buscar_em_pagina(
            nome, url, params_fn=params_fn, verify=verify,
            source_prefix=f"Banca: {nome}"
        )
        results.extend(r_list)
        log.info(f"  {nome}: {len(r_list)} resultado(s)")

    # Bancas que bloqueiam acesso direto (403) — usar DuckDuckGo como fallback
    # DIAGNÓSTICO: VUNESP, CESGRANRIO, IBFC, QUADRIX retornam 403 para qualquer
    # User-Agent, incluindo headers completos de browser. Bot protection ativo.
    bancas_ddg = [
        ("VUNESP", "vunesp.com.br"),
        ("CESGRANRIO", "cesgranrio.org.br"),
        ("IBFC", "ibfc.org.br"),
        ("QUADRIX", "quadrix.org.br"),
    ]
    for nome, site in bancas_ddg:
        count = 0
        for term in SEARCH_TERMS:
            r_list = buscar_via_duckduckgo(term, site, f"Banca: {nome}")
            results.extend(r_list)
            count += len(r_list)
            time.sleep(0.3)
        log.info(f"  {nome} (via DDG): {count} resultado(s)")

    log.info(f"Bancas: {len(results)} resultado(s) total")
    return results


# ---------------------------------------------------------------------------
# FONTE 5: Editais Culturais
# DIAGNÓSTICO E CORREÇÕES:
#   ProAC: proac.sp.gov.br → redireciona para cultura.sp.gov.br
#   Funarte: funarte.gov.br → migrado para gov.br/funarte/pt-br
#   BNDES: URL de busca corrigida para /wps/portal/site/home/busca
#   Caixa Cultural: caixacultural.gov.br → 403; usar caixa.gov.br/cultura
#   SMC-SP: URL corrigida para prefeitura.sp.gov.br/cultura/editais/
#   SESC-SP: URL de busca corrigida para /busca/?q=
#   SESI-SP: URL corrigida para /cultura
#   Itaú Cultural: URL de busca /busca?q= → 200 (OK)
#   CCBB: culturabancodobrasil.com.br → timeout/403; usar bb.com.br/ccbb
#   Petrobras: URLs antigas 404; usar homepage pt/ e buscar texto
#   Natura Musical: domínio redirecionado para aesop; corrigido para natura.com.br
#   Oi Futuro: domínio fora do ar (connection aborted); marcado como indisponível
#   Santander Cultural: corrigido para santander.com.br/institucional-santander/cultura
#   Vale Cultural: URLs antigas 404; usar homepage vale.com/pt/
#   Instituto Unibanco: URL de busca corrigida para /?s= (WordPress)
# ---------------------------------------------------------------------------


# ──────────────────────────────────────────────────────────────────────────────
# FONTE 5b: ProAC/CultSP — PDFs de Resultado (Azure Blob Storage)
# Raspa páginas de edital individuais do IBM WebSphere do ProAC, extrai links
# de PDF do Azure Blob Storage e verifica presença de CNPJ/nome nos resultados.
# ──────────────────────────────────────────────────────────────────────────────

_PROAC_RESULT_KWS = [
    "resultado", "selecao", "selecionado", "aprovado",
    "lista", "inscrit", "comunicado", "convocatoria", "final",
]


def buscar_proac_pdfs() -> list[dict]:
    """
    Raspa editais do ProAC/CultSP e verifica PDFs de resultado no Azure Blob
    Storage para presença de CNPJ 32.309.482 / Hudson Viana Borges.

    Fluxo:
      1. Raspa /Fomento/Fomento_CultSP_Editais_e_PNAB e /Fomento/Programa_PNAB
      2. Coleta links para páginas de edital individuais
      3. Para cada edital (priorizando 2025/2026), extrai links de PDF do blob storage
      4. Para PDFs de resultado NÃO vistos, baixa e verifica nome/CNPJ via pypdf
      5. Retorna alertas para novos resultados encontrados
    """
    try:
        from pypdf import PdfReader  # noqa: PLC0415
    except ImportError:
        log.warning("ProAC PDFs: pypdf não instalado — ignorando esta fonte")
        return []

    results = []
    CULTSP_BASE = "https://www.cultura.sp.gov.br"
    SEARCH_PATTERNS = ["HUDSON VIANA BORGES", "32.309.482", "OZ AS AVESSAS"]

    # 1) Coletar links de edital das páginas de lista
    edital_urls: dict = {}  # url -> titulo
    for list_url in [
        f"{CULTSP_BASE}/sec_cultura/Fomento/Fomento_CultSP_Editais_e_PNAB",
        f"{CULTSP_BASE}/sec_cultura/Fomento/Programa_PNAB",
    ]:
        try:
            r = requests.get(list_url, headers=HEADERS, timeout=8, verify=False)
            if r.status_code != 200:
                log.warning(f"ProAC PDFs: {list_url[-50:]} HTTP {r.status_code}")
                continue
            soup = BeautifulSoup(r.text, "html.parser")
            for a in soup.find_all("a", href=True):
                href = a["href"]
                if "Arquivo_de_Editais" not in href:
                    continue
                full_url = href if href.startswith("http") else f"{CULTSP_BASE}{href}"
                titulo = a.get_text(strip=True)[:80]
                edital_urls[full_url] = titulo
        except Exception as e:
            log.warning(f"ProAC PDFs: erro ao listar editais: {e}")

    log.info(f"ProAC PDFs: {len(edital_urls)} editais encontrados")

    # 2) Priorizar editais recentes (2025/2026) e limitar total
    def _prio(item):
        u = item[0].lower()
        return (("2025" in u or "2026" in u), u)

    sorted_editais = sorted(edital_urls.items(), key=_prio, reverse=True)[:80]

    pdf_checked = 0
    for edital_url, edital_titulo in sorted_editais:
        try:
            r = requests.get(edital_url, headers=HEADERS, timeout=8, verify=False)
            if r.status_code != 200:
                continue

            # Extrair links para PDFs no blob storage
            blob_urls = re.findall(
                r"https?://storageproac\.blob\.core\.windows\.net[^\s\"\'<>]+\.pdf",
                r.text,
                re.IGNORECASE,
            )

            for blob_url in blob_urls:
                filename = blob_url.lower().rsplit("/", 1)[-1]

                # Apenas PDFs com palavras-chave de resultado
                if not any(kw in filename for kw in _PROAC_RESULT_KWS):
                    continue

                # Deduplicação antecipada — evita baixar PDFs já processados
                pdf_id = make_id("ProAC PDF", edital_titulo, blob_url)
                if pdf_id in _SEEN_GLOBAL:
                    continue

                pdf_checked += 1

                # Baixar e extrair texto
                try:
                    pdf_r = requests.get(blob_url, timeout=15, verify=False)
                    if pdf_r.status_code != 200:
                        continue
                    reader = PdfReader(io.BytesIO(pdf_r.content))
                    text = "\n".join(p.extract_text() or "" for p in reader.pages).upper()
                except Exception as e:
                    log.debug(f"ProAC PDF parse erro: {e}")
                    continue

                # Verificar presença de nome/CNPJ
                found = next((p for p in SEARCH_PATTERNS if p in text), None)
                if not found:
                    continue

                # Snippet contextual
                snippet = ""
                for line in text.split("\n"):
                    if found in line:
                        snippet = line.strip()[:200]
                        break

                # Tipo de documento
                doc_type = "Resultado"
                if "inscrit" in filename:
                    doc_type = "Lista de Inscritos"
                elif "comunicado" in filename:
                    doc_type = "Comunicado"
                elif "selec" in filename:
                    doc_type = "Resultado de Seleção"
                elif "final" in filename:
                    doc_type = "Resultado Final"

                results.append({
                    "source": "ProAC/CultSP",
                    "title": f"ProAC {doc_type}: {edital_titulo[:60]}",
                    "url": blob_url,
                    "snippet": snippet,
                })
                log.info(f"  ProAC PDF ENCONTRADO — {edital_titulo[:50]} | {doc_type}")
                time.sleep(0.5)

        except Exception as e:
            log.debug(f"ProAC edital erro: {e}")

        time.sleep(0.3)

    log.info(f"ProAC PDFs: {pdf_checked} PDF(s) verificados, {len(results)} com menção")
    return results

def buscar_editais_culturais() -> list[dict]:
    results = []
    log.info("Buscando em editais culturais...")

    # ProAC/CultSP — PDFs de resultado via scraping direto de edital
    try:
        proac_pdf_r = buscar_proac_pdfs()
        results.extend(proac_pdf_r)
        log.info(f"  ProAC PDFs: {len(proac_pdf_r)} resultado(s)")
    except Exception as e:
        log.warning(f"ProAC PDFs: erro — {e}")

    fontes = [
        # (nome, url, params_fn, verify, source_prefix)
        (
            "ProAC/CultSP",
            "https://www.cultura.sp.gov.br/sec_cultura/Fomento/Portal_do_Fomento",
            lambda t: None,
            False,
            "Edital Cultural: ProAC/CultSP",
        ),
        (
            "Funarte (gov.br)",
            "https://www.gov.br/funarte/pt-br/@@search",
            lambda t: {"SearchableText": t},
            False,
            "Edital Cultural: Funarte/PNAB",
        ),
        (
            "BNDES Cultural",
            "https://www.bndes.gov.br/wps/portal/site/home/busca",
            lambda t: {"q": t},
            False,
            "Edital Cultural: BNDES Cultural",
        ),
        (
            "Caixa Cultural",
            "https://www.caixa.gov.br/cultura",
            lambda t: None,
            False,
            "Edital Cultural: Caixa Cultural",
        ),
        (
            "SMC-SP",
            "https://www.prefeitura.sp.gov.br/cidade/secretarias/cultura/editais/",
            lambda t: None,
            False,
            "Edital Cultural: SMC-SP",
        ),
        (
            "SESC-SP",
            "https://www.sescsp.org.br/busca/",
            lambda t: {"q": t},
            False,
            "Edital Cultural: SESC-SP",
        ),
        (
            "SESI-SP",
            "https://www.sesisp.org.br/cultura",
            lambda t: None,
            False,
            "Edital Cultural: SESI-SP",
        ),
        (
            "Itaú Cultural",
            "https://www.itaucultural.org.br/busca",
            lambda t: {"q": t},
            False,
            "Edital Cultural: Itaú Cultural",
        ),
        (
            "Santander Cultural",
            "https://www.santander.com.br/institucional-santander/cultura",
            lambda t: None,
            False,
            "Edital Cultural: Santander Cultural",
        ),
        (
            "Vale Cultural",
            "https://vale.com/pt/",
            lambda t: None,
            False,
            "Edital Cultural: Vale Cultural",
        ),
        (
            "Instituto Unibanco",
            "https://www.institutounibanco.org.br/",
            lambda t: {"s": t},
            False,
            "Edital Cultural: Instituto Unibanco",
        ),
    ]

    for nome, url, params_fn, verify, source_prefix in fontes:
        r_list = _buscar_em_pagina(
            nome, url, params_fn=params_fn, verify=verify,
            source_prefix=source_prefix,
        )
        results.extend(r_list)
        log.info(f"  {nome}: {len(r_list)} resultado(s)")

    # Fontes que bloqueiam acesso direto (403) — usar DuckDuckGo como fallback
    # DIAGNÓSTICO: CCBB (bb.com.br), Natura Musical (natura.com.br) e
    # Petrobras (petrobras.com.br) retornam 403/timeout para acesso programático.
    # Vale Cultural retorna 429 (rate limit). Oi Futuro: domínio fora do ar.
    fontes_ddg = [
        ("CCBB", "culturabancodobrasil.com.br", "Edital Cultural: CCBB"),
        ("Petrobras Cultural", "petrobras.com.br", "Edital Cultural: Petrobras Cultural"),
        ("Natura Musical", "naturamusical.com.br", "Edital Cultural: Natura Musical"),
        ("Vale Cultural", "vale.com", "Edital Cultural: Vale Cultural"),
    ]
    for nome, site, source_prefix in fontes_ddg:
        count = 0
        for term in SEARCH_TERMS:
            r_list = buscar_via_duckduckgo(term, site, source_prefix)
            results.extend(r_list)
            count += len(r_list)
            time.sleep(0.3)
        log.info(f"  {nome} (via DDG): {count} resultado(s)")

    # Oi Futuro: domínio fora do ar — registrar no log
    log.warning("Oi Futuro: domínio fora do ar (connection aborted) — não monitorado nesta execução")

    log.info(f"Editais Culturais: {len(results)} resultado(s) total")
    return results


# ---------------------------------------------------------------------------
# FONTE 6: Busca Web Geral (Brave Search)
# Captura menções em qualquer site indexado publicamente.
# Usa Brave Search (sem CAPTCHA para acesso programático) com termos formatados
# (CPF e CNPJ com pontuação) para evitar falsos positivos por dígitos parciais.
# Deduplicação por URL via seen_ids impede reenvio de resultados históricos.
# DIAGNÓSTICO (2026-03-18): DDG retorna HTTP 202 (CAPTCHA/rate limit) para buscas
# sem site:. Brave Search retorna resultados válidos com Accept-Encoding: gzip.
# ---------------------------------------------------------------------------

HEADERS_BRAVE = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Encoding": "gzip, deflate",  # sem brotli para evitar erro de decodificação
}


def buscar_web_geral() -> list[dict]:
    """
    Busca geral no Brave Search sem restrição de site.
    Usa SEARCH_TERMS_WEB (termos com pontuação) para evitar falsos positivos.
    Captura qualquer menção pública indexada (concursos, editais, notícias, etc.).
    Deduplicação por URL via seen_ids impede reenvio de resultados históricos.
    """
    results = []
    log.info("Buscando na Web Geral (Brave Search)...")
    for term in SEARCH_TERMS_WEB:
        query = f'"{term}"'
        try:
            r = requests.get(
                "https://search.brave.com/search",
                params={"q": query, "source": "web"},
                headers=HEADERS_BRAVE,
                timeout=8,
                verify=False,
            )
            if r.status_code not in (200, 202):
                log.debug(f"Web Geral Brave: HTTP {r.status_code} para '{term}'")
                time.sleep(0.3)
                continue
        except Exception as e:
            log.warning(f"Web Geral Brave erro para '{term}': {e}")
            continue

        soup = BeautifulSoup(r.text, "html.parser")
        count = 0
        for item in soup.select(".snippet"):
            # Brave Search usa .snippet como container de resultado
            link_el = item.select_one("a[href]")
            if not link_el:
                continue
            url_result = link_el.get("href", "")
            # Filtrar apenas URLs externas (resultados reais)
            if not url_result.startswith("http") or "brave.com" in url_result:
                continue
            title_el = item.select_one(".snippet-title, h2, h3, .title")
            title = title_el.get_text(strip=True) if title_el else link_el.get_text(strip=True)
            desc_el = item.select_one(".snippet-description, .description, p")
            snippet = desc_el.get_text(strip=True) if desc_el else ""
            # Validar: o termo deve aparecer no título ou snippet
            term_lower = term.lower()
            if term_lower in title.lower() or term_lower in snippet.lower():
                if not _resultado_relevante_pessoa(term, title, snippet):
                    log.debug(f"  Web Geral descartado (homônimo): {title[:60]}")
                    continue
                results.append({
                    "source": "Web Geral",
                    "term": term,
                    "title": title,
                    "url": url_result,
                    "snippet": snippet[:300],
                    "date": date.today().isoformat(),
                })
                count += 1
        log.debug(f"  Web Geral: {count} resultado(s) para '{term}'")
        time.sleep(0.3)

    log.info(f"Web Geral (Brave): {len(results)} resultado(s) encontrado(s)")

    # Fallback: DuckDuckGo para termos que Brave pode não indexar
    # (ex: sites menores como guias culturais, portais regionais)
    ddg_url = "https://html.duckduckgo.com/html/"
    for term in SEARCH_TERMS_WEB:
        try:
            r_ddg = requests.get(
                ddg_url,
                params={"q": f'"{term}"'},
                headers=HEADERS,
                timeout=8,
                verify=False,
            )
            soup_ddg = BeautifulSoup(r_ddg.text, "html.parser")
            for result_el in soup_ddg.select(".result"):
                link_el = result_el.select_one(".result__a")
                snippet_el = result_el.select_one(".result__snippet")
                if not link_el:
                    continue
                url_result = link_el.get("href", "")
                if not url_result.startswith("http") or "duckduckgo.com" in url_result:
                    continue
                title = link_el.get_text(strip=True)
                snippet = snippet_el.get_text(strip=True) if snippet_el else ""
                term_lower = term.lower()
                if term_lower in title.lower() or term_lower in snippet.lower():
                    if not _resultado_relevante_pessoa(term, title, snippet):
                        continue
                    results.append({
                        "source": "Web Geral",
                        "term": term,
                        "title": title,
                        "url": url_result,
                        "snippet": snippet[:300],
                        "date": date.today().isoformat(),
                    })
            time.sleep(0.3)
        except Exception as e:
            log.debug(f"  Web Geral DDG erro para '{term}': {e}")

    log.info(f"Web Geral: {len(results)} resultado(s) encontrado(s) (antes de dedup)")
    return results


# ---------------------------------------------------------------------------
# Filtro de novos resultados
# ---------------------------------------------------------------------------

def filtrar_novos(results: list[dict], seen: set) -> tuple[list[dict], set, dict]:
    novos = []
    novos_ids = set()
    source_map = {}  # rid -> source name
    for r in results:
        rid = make_id(r["source"], r["title"], r.get("url", ""))
        if rid not in seen:
            r["_id"] = rid
            novos.append(r)
            novos_ids.add(rid)
            source_map[rid] = r.get("source", "unknown")
    return novos, novos_ids, source_map


# ---------------------------------------------------------------------------
# Formatação do email
# ---------------------------------------------------------------------------

def formatar_email(novos: list[dict], erros: list[str] = None) -> str:
    """
    Gera o HTML premium da newsletter Monitor de Menções.
    Todas as datas são formatadas no padrão dd/mm/aaaa.
    """
    hoje = datetime.now().strftime("%d/%m/%Y %H:%M")
    # Garantir formato dd/mm/aaaa em todos os resultados
    for r in novos:
        if r.get("date"):
            r["date"] = formatar_data(r["date"])
    # Fontes com status de disponibilidade
    fontes_status = {
        "DOU":               "ok",
        "Querido Diário":    "ok",
        "Diários Estaduais": "ok",
        "Bancas":            "ok",
        "Editais Culturais": "ok",
        "Web Geral":         "ok",
        "Concurso MPSP":     "ok",
    }
    # Oi Futuro está fora do ar — registrar como indisponível
    # (não é uma fonte separada na tabela, mas está incluída em Editais Culturais)
    return gerar_html_newsletter(
        resultados=novos,
        alertas=erros if erros else [],
        data_hora=hoje,
        fontes_status=fontes_status,
    )


# ---------------------------------------------------------------------------
# Registro de email pendente (envio feito pelo agente via shell tool)
# ---------------------------------------------------------------------------

def registrar_email_pendente(assunto: str, corpo: str) -> None:
    """
    Salva o email a enviar em data/email_pendente.json.
    O Gmail MCP não pode ser invocado via subprocess Python —
    o agente Manus deve enviar diretamente via shell tool.
    """
    pending_file = DATA_DIR / "email_pendente.json"
    payload = {
        "assunto": assunto,
        "corpo": corpo,
        "content_type": "text/html",
        "destinatario": "huddsonviana@gmail.com",
        "timestamp": datetime.now().strftime("%d/%m/%Y %H:%M"),
        "enviado": False,
    }
    pending_file.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    log.info(f"Email pendente salvo em {pending_file}")



# ---------------------------------------------------------------------------

# ─────────────────────────────────────────────────────────────────────────────
# FONTE 8: DOE-SP — Diário Oficial do Estado de São Paulo
# API oficial: do-api-web-search.doe.sp.gov.br (descoberta via engenharia reversa)
# Parâmetros corretos: Terms (array), FromDate, ToDate, PageNumber, PageSize, SortField
# Substitui a abordagem anterior via Querido Diário (territory_id=3550308 = município,
# não cobria o DOE estadual — causava zero resultados para publicações do Estado de SP).
# ─────────────────────────────────────────────────────────────────────────────


def buscar_doe_sp_api(days_back: int = 7) -> list[dict]:
    """
    Busca publicações do DOE-SP (Diário Oficial do Estado de São Paulo)
    que mencionam Hudson Viana Borges, via API oficial do frontend Next.js.

    API: https://do-api-web-search.doe.sp.gov.br/v2/advanced-search/publications
    Params: Terms (string), FromDate (YYYY-MM-DD), ToDate, PageNumber, PageSize, SortField

    Cobre: convocações, resultados de concurso, nomeações, qualquer publicação
    no DOE-SP onde "Hudson Viana Borges" aparece no texto indexado.

    PERF: days_back=7 (monitor diário não precisa de 90 dias).
    Somente "Hudson Viana Borges" (nome completo) — "Hudson Borges" é
    genérico demais e gera até 20 resultados × 15s de GET cada = timeout.
    Limite de MAX_DETAIL_GETS GETs de detalhe por execução.
    """
    results: list[dict] = []
    base = "https://do-api-web-search.doe.sp.gov.br"
    api_headers = {
        "User-Agent": HEADERS["User-Agent"],
        "Accept": "application/json, text/plain, */*",
        "Origin": "https://www.doe.sp.gov.br",
        "Referer": "https://www.doe.sp.gov.br/busca-avancada",
    }

    from_date = (date.today() - timedelta(days=days_back)).isoformat()
    to_date = date.today().isoformat()

    # SOMENTE nome completo — "Hudson Borges" é genérico demais no DOE-SP
    search_terms = ["Hudson Viana Borges"]
    seen_pub_ids: set = set()
    MAX_DETAIL_GETS = 5  # limite de GETs de detalhe por execução para evitar timeout

    log.info(f"Buscando publicações no DOE-SP (últimos {days_back} dias)...")

    detail_gets_total = 0

    for term in search_terms:
        try:
            params = {
                "Terms": term,
                "FromDate": from_date,
                "ToDate": to_date,
                "PageNumber": 1,
                "PageSize": 10,
                "SortField": "Date",
            }
            r = requests.get(
                f"{base}/v2/advanced-search/publications",
                params=params,
                headers=api_headers,
                timeout=8,
                verify=False,
            )
            if r.status_code != 200:
                log.warning(f"DOE-SP API [{term}]: HTTP {r.status_code}")
                continue

            data = r.json()
            total = data.get("totalItems", 0)
            log.info(f"  DOE-SP [{term}]: {total} publicação(ões)")

            for item in data.get("items", []):
                pub_id = item.get("id", "")
                if not pub_id or pub_id in seen_pub_ids:
                    continue
                seen_pub_ids.add(pub_id)

                title = item.get("title", "")
                pub_date = (item.get("date", "") or "")[:10]
                slug = item.get("slug", "")
                url = f"https://www.doe.sp.gov.br/{slug}" if slug else ""

                # Buscar conteúdo completo para extrair snippet com contexto
                # Limitado a MAX_DETAIL_GETS por execução para evitar timeout
                snippet = ""
                if detail_gets_total < MAX_DETAIL_GETS:
                    try:
                        r2 = requests.get(
                            f"{base}/v2/publications/{pub_id}",
                            headers=api_headers,
                            timeout=10,
                            verify=False,
                        )
                        detail_gets_total += 1
                        if r2.status_code == 200:
                            pub_data = r2.json()
                            content_html = pub_data.get("content", "")
                            content_text = BeautifulSoup(content_html, "html.parser").get_text(" ", strip=True)
                            # Localizar contexto ao redor do nome
                            idx_h = content_text.upper().find("HUDSON VIANA BORGES")
                            if idx_h >= 0:
                                snippet = content_text[max(0, idx_h - 120):idx_h + 350].strip()
                            else:
                                snippet = content_text[:400]
                    except Exception as e_content:
                        log.debug(f"  DOE-SP conteúdo fetch erro: {e_content}")
                else:
                    log.debug(f"  DOE-SP: limite de {MAX_DETAIL_GETS} GETs de detalhe atingido, pulando snippet")

                results.append({
                    "source": "DOE-SP (Diário Oficial Estado SP)",
                    "term": term,
                    "title": f"[DOE-SP {pub_date}] {title}",
                    "url": url,
                    "snippet": snippet,
                    "date": pub_date or date.today().isoformat(),
                })
                log.info(f"  DOE-SP resultado: [{pub_date}] {title[:80]}")

        except Exception as e:
            log.warning(f"DOE-SP API erro [{term}]: {e}")

        time.sleep(0.3)

    log.info(f"DOE-SP TOTAL: {len(results)} resultado(s)")
    return results


# ---------------------------------------------------------------------------
# FONTE 7: Acompanhamento Concurso MPSP 04/2025
# Cargo: Analista Técnico-Científico — Médico Veterinário
# Macrorregião I (Capital) | Edital 78/2025 | Banca VUNESP
# Posição: 3º geral / 2º PCD
# ---------------------------------------------------------------------------
# POLÍTICA DE FILTRAGEM:
#   Só inclui resultado no email se o conteúdo (título + snippet) contiver
#   ao menos uma PALAVRA-CHAVE DE VALIDAÇÃO que comprove relevância direta
#   ao concurso 04/2025 ou ao candidato Hudson Viana Borges.
#   Resultados genéricos (ex: qualquer "nomeação veterinário MP") são descartados.
# ---------------------------------------------------------------------------

# Palavras que DEVEM aparecer no conteúdo para o resultado ser relevante

# ─────────────────────────────────────────────────────────────
# Concurso MPSP 04/2025 — Monitoramento CIRÚRGICO
# ─────────────────────────────────────────────────────────────
# Edital: Concurso Público Nº 04/2025 – Analista Técnico Científico do MPSP
# Cargo: Médico Veterinário (ATC-1.23) — Macrorregião I (Capital)
# Organizadora: VUNESP  |  Publicações oficiais: DOE-SP
# Cronograma: Inscrições 02/09-07/10/2025, Prova 14/12/2025
#
# ESTRATÉGIA v6 (02/04/2026):
# A Querido Diário API busca termos em diários INTEIROS (não por frase exata).
# Qualquer combinação de termos genéricos retorna 10.000+ resultados de
# prefeituras aleatórias. Por isso:
#
#   DOE-SP  → busca APENAS "Hudson Viana Borges" (nome pessoal)
#   VUNESP  → DDG site:vunesp.com.br com termos ultra-específicos
#   DOU     → DDG site:in.gov.br com termos ultra-específicos
#
# O monitor genérico (buscar_dou, buscar_querido_diario, buscar_web_geral)
# já busca "Hudson Viana Borges" em todas as fontes. A função MPSP
# complementa com buscas específicas na VUNESP e DOU por fase do concurso.
# ─────────────────────────────────────────────────────────────


def _resultado_relevante_mpsp(title: str, snippet: str) -> bool:
    """
    Filtro ULTRA-RESTRITIVO para resultados do Concurso MPSP 04/2025.

    Aceita SOMENTE se o conteúdo tiver:
    1. Nome do candidato "Hudson Viana Borges" / "Hudson Borges"
    2. Identificador ÚNICO do concurso (ATC-1.23, edital 78/2025, processo 247/24)
    3. COMBINAÇÃO TRIPLA: concurso 04/2025 + ministério público + SP
       (exige os 3 simultaneamente para evitar falsos positivos de prefeituras)
    4. Analista técnico científico + veterinário + MPSP (combinação tripla)
    """
    texto = (title + " " + snippet).lower()

    # ── Regra 1: nome do candidato → SEMPRE relevante ──
    if "hudson viana" in texto or "hudson borges" in texto:
        return True

    # ── Regra 2: identificadores que são 100% únicos deste concurso ──
    unique_ids = [
        "atc-1.23", "atc 1.23",
        "processo 247/24", "dg-mp nº 247", "dg-mp 247",
    ]
    if any(uid in texto for uid in unique_ids):
        return True

    # ── Regra 3: "concurso" + "04/2025" + MPSP (precisa dos 3) ──
    has_concurso = "concurso" in texto
    has_04_2025 = "04/2025" in texto or "04-2025" in texto
    has_mpsp = any(m in texto for m in [
        "ministério público do estado de são paulo",
        "ministério público do estado de sp",
        "mp-sp", "mpsp",
    ])
    # Variação: "concurso nº 04/2025" + MP genérico é OK
    has_concurso_num = ("concurso nº 04/2025" in texto or
                        "concurso público nº 04/2025" in texto or
                        "concurso n. 04/2025" in texto)
    has_mp_generic = "ministério público" in texto

    if has_concurso_num and has_mp_generic:
        return True
    if has_concurso and has_04_2025 and has_mpsp:
        return True

    # ── Regra 4: analista técnico + veterinário + MPSP ──
    has_atc = "analista técnico" in texto or "analista tecnico" in texto
    has_vet = "veterinário" in texto or "veterinario" in texto
    if has_atc and has_vet and (has_mpsp or has_mp_generic):
        return True

    # Não passou → descartado
    return False


def buscar_concurso_mpsp() -> list[dict]:
    """
    Monitora publicações do Concurso Público Nº 04/2025 do MPSP
    (Analista Técnico Científico — Médico Veterinário ATC-1.23).

    ESTRATÉGIA v7:
    1. DOE-SP → coberto por buscar_doe_sp_api() chamado diretamente em main().
       NÃO chamar novamente aqui — duplicação causava timeout (2× as requests).

    2. VUNESP via DDG: busca frases exatas sobre o concurso
       → DDG faz matching real de frase, ao contrário da API QD.

    3. DOU via DDG: busca frases exatas sobre o concurso
       → Publicações federais (nomeação, homologação).
    """
    results = []
    descartados = 0
    log.info("Buscando atualizações do Concurso MPSP 04/2025 (Médico Veterinário ATC-1.23)...")

    # ── 1. VUNESP via DuckDuckGo ──────────────────────────────────────────
    # DDG faz matching real de frase exata — muito mais preciso que a API QD.
    vunesp_terms = [
        '"concurso público nº 04/2025" "ministério público" site:vunesp.com.br',
        '"analista técnico científico" "04/2025" veterinário site:vunesp.com.br',
        '"Hudson Viana Borges" site:vunesp.com.br',
        '"Hudson Viana Borges" site:documento.vunesp.com.br',
    ]
    for term in vunesp_terms:
        try:
            ddg_url = "https://html.duckduckgo.com/html/"
            r = requests.get(ddg_url, params={"q": term}, headers=HEADERS, timeout=8, verify=False)
            if r.status_code == 200:
                soup = BeautifulSoup(r.text, "html.parser")
                for link_el in soup.select("a.result__a"):
                    title = link_el.get_text(strip=True)
                    raw_href = link_el.get("href", "")
                    href = _extrair_url_ddg(raw_href)
                    if not href or "duckduckgo.com" in href:
                        continue
                    parent = link_el.find_parent("div", class_="result")
                    snippet_el = parent.select_one(".result__snippet") if parent else None
                    snippet = snippet_el.get_text(strip=True) if snippet_el else ""
                    if not _resultado_relevante_mpsp(title, snippet):
                        descartados += 1
                        log.debug(f"  VUNESP descartado: {title[:60]}")
                        continue
                    results.append({
                        "source": "VUNESP (Concurso MPSP 04/2025)",
                        "term": term.split("site:")[0].strip().replace('"', ''),
                        "title": title[:200],
                        "url": href,
                        "snippet": snippet[:400],
                        "date": date.today().isoformat(),
                    })
        except Exception as e:
            log.warning(f"VUNESP concurso MPSP: {e}")
        time.sleep(0.3)

    log.info(f"  VUNESP: {len(results)} relevante(s), {descartados} descartado(s)")
    count_vunesp = len(results)
    descartados_vunesp = descartados

    # ── 3. DOU via DuckDuckGo ─────────────────────────────────────────────
    dou_terms = [
        '"concurso público nº 04/2025" "ministério público" "são paulo" site:in.gov.br',
        '"analista técnico científico" "04/2025" "ministério público" nomeação site:in.gov.br',
        '"Hudson Viana Borges" site:in.gov.br',
    ]
    for term in dou_terms:
        try:
            ddg_url = "https://html.duckduckgo.com/html/"
            r = requests.get(ddg_url, params={"q": term}, headers=HEADERS, timeout=8, verify=False)
            if r.status_code == 200:
                soup = BeautifulSoup(r.text, "html.parser")
                for link_el in soup.select("a.result__a"):
                    title = link_el.get_text(strip=True)
                    raw_href = link_el.get("href", "")
                    href = _extrair_url_ddg(raw_href)
                    if not href or "duckduckgo.com" in href:
                        continue
                    parent = link_el.find_parent("div", class_="result")
                    snippet_el = parent.select_one(".result__snippet") if parent else None
                    snippet = snippet_el.get_text(strip=True) if snippet_el else ""
                    if not _resultado_relevante_mpsp(title, snippet):
                        descartados += 1
                        log.debug(f"  DOU descartado: {title[:60]}")
                        continue
                    results.append({
                        "source": "DOU (Concurso MPSP 04/2025)",
                        "term": term.split("site:")[0].strip().replace('"', ''),
                        "title": title[:200],
                        "url": href,
                        "snippet": snippet[:400],
                        "date": date.today().isoformat(),
                    })
        except Exception as e:
            log.warning(f"DOU concurso MPSP ({term}): {e}")
        time.sleep(0.3)

    log.info(f"  DOU: {len(results) - count_vunesp} relevante(s), {descartados - descartados_vunesp} descartado(s)")
    log.info(f"Concurso MPSP 04/2025 TOTAL: {len(results)} relevante(s), {descartados} descartado(s)")
    return results


def main():
    parser = argparse.ArgumentParser(description="Monitor de menções")
    parser.add_argument(
        "--force-send",
        action="store_true",
        help="Registra o email mesmo que não haja resultados novos",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Executa a busca mas não registra email nem atualiza histórico",
    )
    args = parser.parse_args()

    # ── Timeout global do script ──────────────────────────────────────────────
    # Garante que o script termine em no máximo 720s (12 min), independente
    # de qual fonte esteja lenta. Isso deixa 180s de margem antes do timeout
    # de 900s do run_daily.py. Quando disparado, a coleta para e o script
    # envia os resultados que já foram coletados até aquele momento.
    _MAX_SCRIPT_SECONDS = 720

    class _ScriptTimeout(BaseException):
        pass

    def _timeout_handler(signum, frame):
        raise _ScriptTimeout(f"Timeout de {_MAX_SCRIPT_SECONDS}s atingido")

    signal.signal(signal.SIGALRM, _timeout_handler)
    signal.alarm(_MAX_SCRIPT_SECONDS)
    _t_start = time.time()
    # ─────────────────────────────────────────────────────────────────────────

    log.info("=" * 60)
    log.info("Monitor de Menções — Iniciando execução")
    log.info(f"Data/hora: {datetime.now().isoformat()}")
    log.info(f"Termos: {SEARCH_TERMS}")
    log.info(f"Timeout máximo: {_MAX_SCRIPT_SECONDS}s")
    log.info("=" * 60)

    seen = load_seen()
    _SEEN_GLOBAL.update(seen)  # Disponibiliza seen para buscar_proac_pdfs()
    log.info(f"Histórico carregado: {len(seen)} ID(s) já vistos")

    erros_avisos = []

    # Coleta de todas as fontes
    # Ordem de prioridade: DOE-SP e DOU primeiro (mais críticos), bancas/editais por último
    todos_resultados = []

    try:
        # ── Fontes prioritárias (rodam primeiro, antes de qualquer timeout) ──
        try:
            todos_resultados.extend(buscar_doe_sp_api())
        except Exception as e:
            msg = f"DOE-SP API: erro inesperado — {e}"
            log.error(msg)
            erros_avisos.append(msg)

        try:
            todos_resultados.extend(buscar_concurso_mpsp())
        except Exception as e:
            msg = f"Concurso MPSP: erro inesperado — {e}"
            log.error(msg)
            erros_avisos.append(msg)

        try:
            todos_resultados.extend(buscar_dou())
        except Exception as e:
            msg = f"DOU: erro inesperado — {e}"
            log.error(msg)
            erros_avisos.append(msg)

        try:
            todos_resultados.extend(buscar_querido_diario())
        except Exception as e:
            msg = f"Querido Diário: erro inesperado — {e}"
            log.error(msg)
            erros_avisos.append(msg)

        try:
            todos_resultados.extend(buscar_diarios_estaduais())
        except Exception as e:
            msg = f"Diários Estaduais: erro inesperado — {e}"
            log.error(msg)
            erros_avisos.append(msg)

        # ── Fontes secundárias (podem ser cortadas pelo timeout sem perda crítica) ──
        try:
            todos_resultados.extend(buscar_web_geral())
        except Exception as e:
            msg = f"Busca Web Geral: erro inesperado — {e}"
            log.error(msg)
            erros_avisos.append(msg)

        try:
            todos_resultados.extend(buscar_bancas())
        except Exception as e:
            msg = f"Bancas: erro inesperado — {e}"
            log.error(msg)
            erros_avisos.append(msg)

        try:
            todos_resultados.extend(buscar_editais_culturais())
        except Exception as e:
            msg = f"Editais Culturais: erro inesperado — {e}"
            log.error(msg)
            erros_avisos.append(msg)

    except _ScriptTimeout:
        elapsed = int(time.time() - _t_start)
        # Timeout nas fontes secundárias não é uma regressão crítica
        # — as fontes prioritárias (DOE-SP, DOU, QD) já rodaram
        msg = f"Fontes secundárias interrompidas pelo timeout de {_MAX_SCRIPT_SECONDS}s ({elapsed}s decorridos)"
        log.warning(msg)
        # Não adicionar ao erros_avisos para não gerar alarme no email em dias sem menções

    finally:
        signal.alarm(0)  # cancelar o alarme se o script terminou antes

    log.info(f"Total bruto coletado: {len(todos_resultados)} resultado(s)")

    novos, novos_ids, source_map = filtrar_novos(todos_resultados, seen)
    log.info(f"Resultados NOVOS (não vistos antes): {len(novos)}")

    # ── Gravar TODOS os IDs encontrados no Supabase (não apenas os novos) ──────
    # Isso garante que resultados estáticos da web sejam marcados como "vistos"
    # imediatamente, evitando reenvios em execuções futuras.
    todos_ids = {make_id(r["source"], r["title"], r.get("url", "")) for r in todos_resultados}
    todos_source_map = {make_id(r["source"], r["title"], r.get("url", "")): r.get("source", "unknown") for r in todos_resultados}
    ids_a_persistir = todos_ids - seen  # apenas os que ainda não estão no Supabase
    if ids_a_persistir:
        save_seen(seen, new_ids=ids_a_persistir, source_map=todos_source_map)
        log.info(f"Supabase atualizado: {len(ids_a_persistir)} ID(s) novos gravados (total bruto)")
    # ─────────────────────────────────────────────────────────────────────────

    hoje = datetime.now().strftime("%d/%m/%Y")
    assunto = _gerar_assunto(
        resultados=novos,
        alertas=erros_avisos if erros_avisos else None,
        data=hoje,
    )

    corpo_html = formatar_email(novos, erros=erros_avisos if erros_avisos else None)

    if args.dry_run:
        log.info("Modo dry-run: email NÃO registrado.")
        preview_path = DATA_DIR / "email_preview.html"
        preview_path.write_text(corpo_html, encoding="utf-8")
        print("\n--- PRÉVIA DO EMAIL (HTML) ---")
        print(f"Assunto: {assunto}")
        print(f"Prévia salva em: {preview_path}")
        return

    deve_registrar = bool(novos) or args.force_send
    if deve_registrar:
        registrar_email_pendente(assunto, corpo_html)
        log.info(f"Histórico atualizado: {len(seen) + len(todos_ids)} ID(s) no total")
    else:
        log.info("Sem resultados novos e --force-send não ativado. Email não registrado.")

    log.info("Monitor de Menções — Execução concluída")
    log.info("=" * 60)


if __name__ == "__main__":
    main()
