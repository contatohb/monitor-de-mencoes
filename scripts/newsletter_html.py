"""
newsletter_html.py — Gerador de HTML premium para a newsletter do Monitor de Menções.

Responsabilidades:
  - Renderizar o template HTML com os dados da execução do monitor
  - Suportar três estados: sem resultados, com resultados novos, com alertas de regressão
  - Formatar todas as datas no padrão dd/mm/aaaa
  - Retornar HTML pronto para envio via Gmail MCP (content_type: text/html)

Uso:
  from scripts.newsletter_html import gerar_html_newsletter
  html = gerar_html_newsletter(resultados=[], alertas=[], data_hora="18/03/2026 10:00")
"""

from __future__ import annotations

import html as _html
import re
from datetime import date, datetime
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# Helpers de data
# ---------------------------------------------------------------------------

def formatar_data(valor: str) -> str:
    """
    Converte qualquer formato de data reconhecível para dd/mm/aaaa.
    Aceita: 'YYYY-MM-DD', 'YYYY-MM-DD HH:MM', datetime, date.
    Retorna o valor original se não reconhecer o formato.
    """
    if not valor:
        return valor
    # Já está no formato correto
    if re.match(r"^\d{2}/\d{2}/\d{4}", str(valor)):
        return str(valor)
    # ISO 8601: YYYY-MM-DD ou YYYY-MM-DD HH:MM:SS
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})", str(valor))
    if m:
        return f"{m.group(3)}/{m.group(2)}/{m.group(1)}"
    return str(valor)


def data_hora_agora() -> str:
    """Retorna data e hora atual no formato dd/mm/aaaa HH:MM."""
    now = datetime.now()
    return now.strftime("%d/%m/%Y %H:%M")


# ---------------------------------------------------------------------------
# Constantes de design (paleta de cores)
# ---------------------------------------------------------------------------

# Estado: nenhum resultado novo
_STATUS_NONE = {
    "icon": "✓",
    "icon_bg": "rgba(16,185,129,0.2)",
    "bar_bg": "#f0fdf4",
    "bar_accent": "#22c55e",
    "bar_text": "#15803d",
    "bar_class": "status-ok-bg",
    "label": "Tudo monitorado",
    "desc": "Nenhuma menção nova encontrada nesta execução.",
}

# Estado: resultados novos encontrados
_STATUS_NEW = {
    "icon": "🔔",
    "icon_bg": "rgba(59,91,219,0.2)",
    "bar_bg": "#eff6ff",
    "bar_accent": "#3b82f6",
    "bar_text": "#1d4ed8",
    "bar_class": "",
    "label": "Menções encontradas",
    "desc": "Novos resultados identificados e listados abaixo.",
}

# Estado: alerta de regressão de fonte
_STATUS_ALERT = {
    "icon": "⚠",
    "icon_bg": "rgba(234,88,12,0.2)",
    "bar_bg": "#fff7ed",
    "bar_accent": "#f97316",
    "bar_text": "#c2410c",
    "bar_class": "alert-bg",
    "label": "Atenção",
    "desc": "Uma ou mais fontes apresentaram falha nesta execução.",
}

# Categorias de fontes e seus ícones
_FONTES = [
    ("DOU",              "Diário Oficial da União",                    "📰"),
    ("Querido Diário",   "Querido Diário (OKFN Brasil)",               "📄"),
    ("Diários Estaduais","Diários Estaduais (SP, RJ, MG, RS, PR…)",   "🗂"),
    ("Bancas",           "Bancas de concurso (IADES, FGV, CEBRASPE…)","📋"),
    ("Editais Culturais","Editais culturais (ProAC, Funarte, BNDES…)", "🎭"),
    ("Web Geral",        "Busca Web Geral (Brave Search)",             "🌐"),
]


# ---------------------------------------------------------------------------
# Blocos HTML reutilizáveis
# ---------------------------------------------------------------------------

def _tag(texto: str) -> str:
    """Renderiza uma tag/pill de categoria de fonte."""
    return (
        f'<span style="display:inline-block;background-color:#eff2ff;color:#3b5bdb;'
        f'font-family:\'Helvetica Neue\',Helvetica,Arial,sans-serif;font-size:11px;'
        f'font-weight:600;padding:3px 10px;border-radius:20px;margin:2px 4px 2px 0;">'
        f'{_html.escape(texto)}</span>'
    )


def _bloco_sem_resultados() -> str:
    return """
    <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0">
      <tr>
        <td style="padding:48px 40px;text-align:center;">
          <div style="width:64px;height:64px;border-radius:50%;background-color:#f0fdf4;
                      margin:0 auto 20px auto;line-height:64px;font-size:30px;text-align:center;">
            ✓
          </div>
          <h2 style="font-family:'Helvetica Neue',Helvetica,Arial,sans-serif;
                     font-size:20px;font-weight:700;color:#111827;margin:0 0 10px 0;">
            Nenhuma menção nova
          </h2>
          <p style="font-family:'Helvetica Neue',Helvetica,Arial,sans-serif;
                    font-size:14px;color:#6b7280;margin:0;line-height:1.6;max-width:380px;
                    margin-left:auto;margin-right:auto;">
            Todas as fontes foram verificadas e nenhuma menção nova aos termos
            monitorados foi identificada nesta execução.
          </p>
        </td>
      </tr>
    </table>
    """


def _bloco_resultado(r: dict, index: int) -> str:
    """Renderiza um card de resultado individual."""
    source = _html.escape(r.get("source", ""))
    term   = _html.escape(r.get("term", ""))
    title  = _html.escape(r.get("title", "Sem título"))
    url    = r.get("url", "#")
    snippet = _html.escape(r.get("snippet", ""))
    data   = formatar_data(r.get("date", ""))

    # Cor de destaque por categoria de fonte
    cor_map = {
        "DOU":               "#1d4ed8",
        "Querido Diário":    "#7c3aed",
        "Diários Estaduais": "#0369a1",
        "Bancas":            "#0f766e",
        "Editais Culturais": "#b45309",
        "Web Geral":         "#1d4ed8",
    }
    cor = cor_map.get(source, "#374151")
    bg_index = "#f8faff" if index % 2 == 0 else "#ffffff"

    snippet_html = (
        f'<p style="font-family:\'Helvetica Neue\',Helvetica,Arial,sans-serif;'
        f'font-size:13px;color:#6b7280;margin:8px 0 0 0;line-height:1.6;">'
        f'…{snippet}…</p>'
    ) if snippet else ""

    return f"""
    <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0">
      <tr>
        <td style="padding:0 40px 0 40px;">
          <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0"
                 style="background-color:{bg_index};border:1px solid #e5e7f0;
                        border-radius:10px;margin:12px 0;" class="result-card">
            <tr>
              <td style="padding:18px 20px 16px 20px;">
                <!-- Cabeçalho do card -->
                <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0">
                  <tr>
                    <td>
                      <span style="display:inline-block;background-color:{cor};color:#ffffff;
                                   font-family:'Helvetica Neue',Helvetica,Arial,sans-serif;
                                   font-size:10px;font-weight:700;padding:3px 9px;
                                   border-radius:4px;text-transform:uppercase;
                                   letter-spacing:0.8px;">
                        {source}
                      </span>
                      <span style="font-family:'Helvetica Neue',Helvetica,Arial,sans-serif;
                                   font-size:11px;color:#9ca3af;margin-left:8px;">
                        Termo: <strong style="color:#6b7280;">{term}</strong>
                      </span>
                    </td>
                    <td align="right">
                      <span style="font-family:'Helvetica Neue',Helvetica,Arial,sans-serif;
                                   font-size:11px;color:#9ca3af;" class="result-meta">
                        {data}
                      </span>
                    </td>
                  </tr>
                </table>
                <!-- Título / link -->
                <a href="{url}" target="_blank"
                   style="font-family:'Helvetica Neue',Helvetica,Arial,sans-serif;
                          font-size:15px;font-weight:600;color:#1a2f6b;
                          text-decoration:none;display:block;margin-top:10px;
                          line-height:1.4;">
                  {title}
                </a>
                {snippet_html}
                <!-- URL curta -->
                <p style="font-family:'Helvetica Neue',Helvetica,Arial,sans-serif;
                          font-size:11px;color:#9ca3af;margin:8px 0 0 0;
                          word-break:break-all;">
                  <a href="{url}" target="_blank"
                     style="color:#3b82f6;text-decoration:none;">
                    {url[:80]}{'…' if len(url) > 80 else ''}
                  </a>
                </p>
              </td>
            </tr>
          </table>
        </td>
      </tr>
    </table>
    """


def _bloco_com_resultados(resultados: list[dict]) -> str:
    """Renderiza todos os cards de resultado."""
    intro = f"""
    <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0">
      <tr>
        <td style="padding:28px 40px 8px 40px;">
          <p style="font-family:'Helvetica Neue',Helvetica,Arial,sans-serif;
                    font-size:11px;font-weight:700;color:#6b7494;
                    text-transform:uppercase;letter-spacing:1.5px;margin:0 0 4px 0;">
            Menções identificadas
          </p>
          <p style="font-family:'Helvetica Neue',Helvetica,Arial,sans-serif;
                    font-size:13px;color:#374151;margin:0;">
            {len(resultados)} novo(s) resultado(s) encontrado(s) nesta execução.
          </p>
        </td>
      </tr>
    </table>
    """
    cards = "".join(_bloco_resultado(r, i) for i, r in enumerate(resultados))
    spacer = '<table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0"><tr><td style="height:20px;"></td></tr></table>'
    return intro + cards + spacer


def _bloco_alerta(alertas: list[str]) -> str:
    """Renderiza o bloco de alertas de regressão de fonte."""
    if not alertas:
        return ""
    itens = "".join(
        f'<li style="font-family:\'Helvetica Neue\',Helvetica,Arial,sans-serif;'
        f'font-size:13px;color:#92400e;margin-bottom:6px;">{_html.escape(a)}</li>'
        for a in alertas
    )
    return f"""
    <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0">
      <tr>
        <td style="padding:20px 40px 0 40px;">
          <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0"
                 style="background-color:#fff7ed;border:1px solid #fed7aa;border-radius:10px;"
                 class="alert-bg">
            <tr>
              <td style="padding:16px 20px;">
                <p style="font-family:'Helvetica Neue',Helvetica,Arial,sans-serif;
                          font-size:12px;font-weight:700;color:#c2410c;
                          text-transform:uppercase;letter-spacing:1px;margin:0 0 10px 0;"
                   class="alert-text">
                  ⚠ Alertas de regressão
                </p>
                <ul style="margin:0;padding-left:18px;">
                  {itens}
                </ul>
              </td>
            </tr>
          </table>
        </td>
      </tr>
    </table>
    """


def _tabela_fontes(fontes_status: Optional[dict] = None) -> str:
    """
    Renderiza a tabela de fontes verificadas.
    fontes_status: dict {nome_fonte: 'ok' | 'falha' | 'indisponivel'}
    """
    if fontes_status is None:
        fontes_status = {}

    linhas = ""
    for chave, descricao, icone in _FONTES:
        status = fontes_status.get(chave, "ok")
        if status == "ok":
            dot_color = "#22c55e"
            status_text = "OK"
            status_color = "#15803d"
        elif status == "indisponivel":
            dot_color = "#f59e0b"
            status_text = "Indisponível"
            status_color = "#92400e"
        else:
            dot_color = "#ef4444"
            status_text = "Falha"
            status_color = "#dc2626"

        linhas += f"""
        <tr>
          <td style="padding:8px 12px 8px 0;vertical-align:middle;width:28px;">
            <span style="font-size:16px;">{icone}</span>
          </td>
          <td style="padding:8px 0;vertical-align:middle;">
            <span style="font-family:'Helvetica Neue',Helvetica,Arial,sans-serif;
                         font-size:13px;color:#374151;">
              {_html.escape(descricao)}
            </span>
          </td>
          <td align="right" style="padding:8px 0;vertical-align:middle;white-space:nowrap;">
            <span style="display:inline-block;width:8px;height:8px;border-radius:50%;
                         background-color:{dot_color};margin-right:6px;
                         vertical-align:middle;"></span>
            <span style="font-family:'Helvetica Neue',Helvetica,Arial,sans-serif;
                         font-size:12px;color:{status_color};font-weight:600;">
              {status_text}
            </span>
          </td>
        </tr>
        <tr>
          <td colspan="3" style="padding:0;">
            <hr style="border:none;border-top:1px solid #f3f4f6;margin:0;" />
          </td>
        </tr>
        """

    return f"""
    <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0"
           style="border:1px solid #e5e7f0;border-radius:10px;overflow:hidden;"
           class="card-border">
      <tr>
        <td style="padding:4px 16px 0 16px;">
          <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0">
            {linhas}
          </table>
        </td>
      </tr>
    </table>
    """


# ---------------------------------------------------------------------------
# Função principal
# ---------------------------------------------------------------------------

def gerar_html_newsletter(
    resultados: list[dict],
    alertas: list[str] | None = None,
    data_hora: str | None = None,
    fontes_status: dict | None = None,
) -> str:
    """
    Gera o HTML completo da newsletter Monitor de Menções Monitor.

    Args:
        resultados:    Lista de dicts com resultados novos do monitor.
        alertas:       Lista de strings descrevendo regressões de fontes.
        data_hora:     String de data/hora no formato dd/mm/aaaa HH:MM.
                       Se None, usa o momento atual.
        fontes_status: Dict {nome_fonte: 'ok'|'falha'|'indisponivel'}.

    Returns:
        String HTML pronta para envio.
    """
    if alertas is None:
        alertas = []
    if data_hora is None:
        data_hora = data_hora_agora()
    else:
        # Garantir formato dd/mm/aaaa
        data_hora = formatar_data(data_hora.split(" ")[0]) + (
            " " + data_hora.split(" ")[1] if " " in data_hora else ""
        )

    # Determinar estado
    if alertas:
        status = _STATUS_ALERT
    elif resultados:
        status = _STATUS_NEW
    else:
        status = _STATUS_NONE

    n_resultados = len(resultados)

    # Bloco de resultados
    if resultados:
        bloco_resultados = _bloco_com_resultados(resultados)
    else:
        bloco_resultados = _bloco_sem_resultados()

    # Bloco de alertas (inserido antes dos resultados se houver)
    bloco_alertas = _bloco_alerta(alertas) if alertas else ""

    # Tabela de fontes
    tabela_fontes = _tabela_fontes(fontes_status)

    # Carregar template base
    template_path = Path(__file__).parent.parent / "templates" / "newsletter.html"
    template = template_path.read_text(encoding="utf-8")

    # Substituir placeholders
    replacements = {
        "{{DATA}}":             data_hora.split(" ")[0] if " " in data_hora else data_hora,
        "{{DATA_HORA}}":        data_hora,
        "{{STATUS_ICON}}":      status["icon"],
        "{{STATUS_ICON_BG}}":   status["icon_bg"],
        "{{STATUS_BAR_BG}}":    status["bar_bg"],
        "{{STATUS_BAR_ACCENT}}":status["bar_accent"],
        "{{STATUS_BAR_TEXT}}":  status["bar_text"],
        "{{STATUS_BAR_CLASS}}": status["bar_class"],
        "{{STATUS_LABEL}}":     status["label"],
        "{{STATUS_DESC}}":      status["desc"],
        "{{N_RESULTADOS}}":     str(n_resultados),
        "{{BLOCO_RESULTADOS}}": bloco_alertas + bloco_resultados,
        "{{TABELA_FONTES}}":    tabela_fontes,
    }

    for placeholder, valor in replacements.items():
        template = template.replace(placeholder, valor)

    return template


# ---------------------------------------------------------------------------
# Gerador de assunto do email
# ---------------------------------------------------------------------------

def gerar_assunto(resultados: list[dict], alertas: list[str] | None = None, data: str | None = None) -> str:
    """
    Gera o assunto do email no formato:
      [Menções] <descrição> — dd/mm/aaaa
    """
    if data is None:
        data = date.today().strftime("%d/%m/%Y")
    else:
        data = formatar_data(data)

    if alertas:
        n = len(alertas)
        return f"[Menções] ⚠ {n} alerta(s) de regressão — {data}"
    elif resultados:
        n = len(resultados)
        return f"[Menções] 🔔 {n} menção(ões) nova(s) — {data}"
    else:
        return f"[Menções] Nenhum resultado novo — {data}"


# ---------------------------------------------------------------------------
# CLI de teste
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    # Gerar prévia HTML de teste com resultado simulado
    resultados_teste = [
        {
            "source": "FGV",
            "term": "Hudson Viana Borges",
            "title": "Lista de indeferidos — Concurso AgSUS (Analista de Gestão — Saúde Pública)",
            "url": "https://conhecimento.fgv.br/sites/default/files/concursos/277-agsus-pcd-indeferidos.pdf",
            "snippet": "277019596 Hudson Viana Borges. Analista de Gestão - AgSUS - Saúde Pública.",
            "date": "2025-09-04",
        },
        {
            "source": "Web Geral",
            "term": "Hudson Viana Borges",
            "title": "Hudson Viana Borges — WeAudition",
            "url": "https://www.weaudition.com/huddson",
            "snippet": "Professional singer and actor active in the market for almost 20 years.",
            "date": "2026-03-18",
        },
    ]

    html_preview = gerar_html_newsletter(
        resultados=resultados_teste,
        data_hora="18/03/2026 10:00",
    )
    out = Path("/tmp/newsletter_preview.html")
    out.write_text(html_preview, encoding="utf-8")
    print(f"Prévia gerada: {out}")

    html_vazio = gerar_html_newsletter(resultados=[], data_hora="18/03/2026 10:00")
    out2 = Path("/tmp/newsletter_preview_vazio.html")
    out2.write_text(html_vazio, encoding="utf-8")
    print(f"Prévia (sem resultados) gerada: {out2}")

    print("Assunto com resultados:", gerar_assunto(resultados_teste))
    print("Assunto sem resultados:", gerar_assunto([]))
