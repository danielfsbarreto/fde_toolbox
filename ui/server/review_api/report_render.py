"""An account report as one HTML document: the app shows it as the preview, and WeasyPrint turns the same HTML into
the PDF. CrewAI's design system for print: EB Garamond headings, Inter body, Geist Mono labels (bundled in
report_assets/fonts), warm white canvas, cards, coral only as small accents.

The content comes from the reports collection, written by the account_report flow (fde_core.domain.reports).
"""

from __future__ import annotations

from datetime import date
from html import escape
from pathlib import Path
from typing import Any

ASSETS = Path(__file__).resolve().parent / "report_assets"

LABELS = {
    "en": {
        "eyebrow": "Account report",
        "account": "Account",
        "accounts": "Accounts",
        "days": "Days covered",
        "developments": "Key developments",
        "open": "Open follow-ups",
        "summary": "Summary",
        "decisions": "Decisions",
        "risks": "Risks and open issues",
        "follow_ups": "Follow-ups still open",
        "what": "Follow-up",
        "owner": "Owner",
        "since": "Since",
        "due": "Due",
        "completed": "{n} follow-ups from the period were completed.",
        "none_open": "No follow-ups still open.",
        "missing": "Not covered (no reviewed log): {days}.",
        "nothing": "No reviewed activity in the period.",
        "footer": "Written from {n} reviewed days of the FDE Activity Log",
        "sources": "sources",
    },
    "pt": {
        "eyebrow": "Relatório de contas",
        "account": "Conta",
        "accounts": "Contas",
        "days": "Dias cobertos",
        "developments": "Principais acontecimentos",
        "open": "Pendências abertas",
        "summary": "Resumo",
        "decisions": "Decisões",
        "risks": "Riscos e pontos em aberto",
        "follow_ups": "Pendências em aberto",
        "what": "Pendência",
        "owner": "Responsável",
        "since": "Desde",
        "due": "Prazo",
        "completed": "{n} pendências do período foram concluídas.",
        "none_open": "Nenhuma pendência em aberto.",
        "missing": "Não cobertos (sem log revisado): {days}.",
        "nothing": "Nenhuma atividade revisada no período.",
        "footer": "Escrito a partir de {n} dias revisados do FDE Activity Log",
        "sources": "fontes",
    },
    "es": {
        "eyebrow": "Informe de cuentas",
        "account": "Cuenta",
        "accounts": "Cuentas",
        "days": "Días cubiertos",
        "developments": "Hechos principales",
        "open": "Pendientes abiertos",
        "summary": "Resumen",
        "decisions": "Decisiones",
        "risks": "Riesgos y temas abiertos",
        "follow_ups": "Pendientes abiertos",
        "what": "Pendiente",
        "owner": "Responsable",
        "since": "Desde",
        "due": "Plazo",
        "completed": "{n} pendientes del período se completaron.",
        "none_open": "Ningún pendiente abierto.",
        "missing": "No cubiertos (sin log revisado): {days}.",
        "nothing": "Sin actividad revisada en el período.",
        "footer": "Escrito a partir de {n} días revisados del FDE Activity Log",
        "sources": "fuentes",
    },
}
MONTHS = {
    "en": ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"],
    "pt": ["jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez"],
    "es": ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"],
}


def _day(value: str | date, language: str, year: bool = False) -> str:
    day = value if isinstance(value, date) else date.fromisoformat(str(value)[:10])
    month = MONTHS.get(language, MONTHS["en"])[day.month - 1]
    if language == "en":
        return f"{month} {day.day}" + (f", {day.year}" if year else "")
    return f"{day.day} {month}" + (f" {day.year}" if year else "")


def _paragraphs(text: str) -> str:
    return "".join(f"<p>{escape(part.strip())}</p>" for part in text.split("\n") if part.strip())


def _list(items: list[str]) -> str:
    return "<ul>" + "".join(f"<li>{escape(item)}</li>" for item in items) + "</ul>"


def _section(section: dict[str, Any], language: str, labels: dict[str, str]) -> str:
    parts = [
        '<section class="account">',
        f'<div class="eyebrow"><span class="pill"></span>{escape(labels["account"])}</div>',
        f"<h2>{escape(section['account_name'])}</h2>",
    ]
    developments = section.get("developments") or []
    if not developments and not section.get("decisions") and not section.get("risks"):
        parts.append(f'<p class="lead">{escape(labels["nothing"])}</p></section>')
        return "".join(parts)
    parts.append(f'<div class="lead">{_paragraphs(section.get("summary", ""))}</div>')

    if developments:
        parts.append(f'<h3>{escape(labels["developments"])}</h3><ol class="timeline">')
        for item in developments:
            links = item.get("links") or []
            sources = (
                ' <span class="sources">'
                + " ".join(f'<a href="{escape(url, quote=True)}">{n}</a>' for n, url in enumerate(links[:6], 1))
                + "</span>"
                if links
                else ""
            )
            parts.append(
                f'<li><span class="date">{escape(_day(item["date"], language))}</span>'
                f'<span class="text">{escape(item["text"])}{sources}</span></li>'
            )
        parts.append("</ol>")

    cards = []
    if section.get("decisions"):
        cards.append(f'<div class="card"><h3>{escape(labels["decisions"])}</h3>{_list(section["decisions"])}</div>')
    if section.get("risks"):
        cards.append(f'<div class="card"><h3>{escape(labels["risks"])}</h3>{_list(section["risks"])}</div>')
    if cards:
        parts.append('<div class="cards">' + "".join(cards) + "</div>")

    follow_ups = section.get("follow_ups") or []
    still_open = [item for item in follow_ups if item.get("todoist") in ("open", "not_sent", "unknown")]
    completed = sum(1 for item in follow_ups if item.get("todoist") == "completed")
    parts.append(f"<h3>{escape(labels['follow_ups'])}</h3>")
    if still_open:
        rows = "".join(
            "<tr>"
            + "<td>"
            + (
                f'<a href="{escape(item["url"], quote=True)}">{escape(item["what"])}</a>'
                if item.get("url")
                else escape(item["what"])
            )
            + "</td>"
            + f"<td>{escape(item.get('owner') or '')}</td>"
            + f'<td class="nowrap">{escape(_day(item["date"], language))}</td>'
            + f"<td>{escape(item.get('due') or '')}</td>"
            + "</tr>"
            for item in still_open
        )
        parts.append(
            f'<table class="follow-ups"><thead><tr><th>{escape(labels["what"])}</th><th>{escape(labels["owner"])}</th>'
            f"<th>{escape(labels['since'])}</th><th>{escape(labels['due'])}</th></tr></thead><tbody>{rows}</tbody></table>"
        )
    else:
        parts.append(f'<p class="muted">{escape(labels["none_open"])}</p>')
    if completed:
        parts.append(f'<p class="muted">{escape(labels["completed"].format(n=completed))}</p>')
    parts.append("</section>")
    return "".join(parts)


def report_html(report: dict[str, Any], for_pdf: bool = False) -> str:
    """The whole report as HTML. for_pdf: fonts and logo by file path (WeasyPrint); otherwise the app serves them."""
    request = report["request"]
    content = report.get("content") or {}
    language = request.get("language", "en") if request.get("language") in LABELS else "en"
    labels = LABELS[language]
    base = ASSETS.as_uri() if for_pdf else "/api/report-assets"
    sections = content.get("sections") or []
    covered = content.get("days_covered") or []
    missing = content.get("days_missing") or []
    developments = sum(len(section.get("developments") or []) for section in sections)
    still_open = sum(
        1
        for section in sections
        for item in section.get("follow_ups") or []
        if item.get("todoist") in ("open", "not_sent", "unknown")
    )
    period = f"{_day(request['start'], language)} – {_day(request['end'], language, year=True)}"

    stats = "".join(
        f'<div class="stat"><div class="num">{value}</div><div class="label">{escape(label)}</div></div>'
        for value, label in (
            (len(sections), labels["accounts"]),
            (len(covered), labels["days"]),
            (developments, labels["developments"]),
            (still_open, labels["open"]),
        )
    )
    missing_note = (
        '<p class="note">'
        + escape(labels["missing"].format(days=", ".join(_day(day, language) for day in missing)))
        + "</p>"
        if missing
        else ""
    )
    body = (
        f'<header><img class="logo" src="{base}/crewai-logo-dark.png" alt="CrewAI">'
        f'<div class="eyebrow"><span class="pill"></span>{escape(labels["eyebrow"])} · {escape(period)}</div>'
        f"<h1>{escape(content.get('title', ''))}</h1>"
        f'<div class="lead overview">{_paragraphs(content.get("overview", ""))}</div>'
        f'<div class="stats">{stats}</div>{missing_note}</header>'
        + "".join(_section(section, language, labels) for section in sections)
        + f"<footer>{escape(labels['footer'].format(n=len(covered)))}</footer>"
    )
    return f"""<!doctype html>
<html lang="{language}"><head><meta charset="utf-8"><title>{escape(content.get("title", "Account report"))}</title>
<style>{_css(base)}</style></head><body>{body}</body></html>"""


def report_pdf(report: dict[str, Any]) -> bytes:
    from weasyprint import HTML  # imported here: only the PDF download needs it

    return HTML(string=report_html(report, for_pdf=True), base_url=str(ASSETS)).write_pdf()


def _css(base: str) -> str:
    fonts = "".join(
        f"@font-face {{ font-family: '{family}'; src: url('{base}/fonts/{file}') format('woff');"
        f" font-weight: {weight}; font-style: {style}; }}"
        for family, file, weight, style in (
            ("EB Garamond", "eb-garamond-latin-500-normal.woff", 500, "normal"),
            ("EB Garamond", "eb-garamond-latin-500-italic.woff", 500, "italic"),
            ("Inter", "inter-latin-400-normal.woff", 400, "normal"),
            ("Inter", "inter-latin-400-italic.woff", 400, "italic"),
            ("Inter", "inter-latin-600-normal.woff", 600, "normal"),
            ("Geist Mono", "geist-mono-latin-400-normal.woff", 400, "normal"),
        )
    )
    return (
        fonts
        + """
:root { --coral: #FF5A50; --black: #000000; --ink: #0D0D0D; --coal: #161616; --ink-soft: #474747; --white: #FFFFFF;
  --offwhite: #F5F3F0; --card: #EDEDED; --border: #CCC5B9; --secondary: #686868; --muted: #8E8E8E;
  --serif: "EB Garamond", Georgia, "Times New Roman", serif; --sans: "Inter", "Carlito", "Calibri", sans-serif;
  --mono: "Geist Mono", "Consolas", monospace; }
@page { size: A4; margin: 18mm 16mm 20mm;
  @bottom-right { content: counter(page) " / " counter(pages); font-family: var(--mono); font-size: 7.5pt;
    color: #8E8E8E; } }
* { box-sizing: border-box; }
html { background: var(--white); }
body { font-family: var(--sans); color: var(--coal); font-size: 10pt; line-height: 1.5; margin: 0; }
@media screen { body { max-width: 820px; margin: 0 auto; padding: 40px 32px 64px; font-size: 15px; } }
h1, h2, h3 { font-family: var(--serif); color: var(--ink); font-weight: 500; margin: 0; }
h1 { font-size: 26pt; line-height: 1.1; margin: 10px 0 14px; }
h2 { font-size: 18pt; line-height: 1.15; margin: 6px 0 10px; }
h3 { font-size: 12.5pt; line-height: 1.25; margin: 18px 0 8px; }
p { margin: 0 0 8px; }
a { color: var(--ink); }
.logo { height: 22px; display: block; margin-bottom: 18px; }
.eyebrow { font-family: var(--mono); font-size: 7.5pt; letter-spacing: 1.5px; text-transform: uppercase;
  color: var(--ink-soft); display: flex; align-items: center; gap: 8px; }
.pill { display: inline-block; width: 16px; height: 6px; border-radius: 999px; background: var(--coral); }
.lead { color: var(--secondary); }
.overview p { font-size: 11pt; color: var(--coal); }
.stats { display: flex; margin: 16px 0 6px; border-top: 1px solid var(--border);
  border-bottom: 1px solid var(--border); }
.stat { flex: 1; padding: 10px 12px; border-left: 1px solid var(--border); }
.stat:first-child { border-left: none; padding-left: 0; }
.stat .num { font-weight: 600; font-size: 20pt; color: var(--black); line-height: 1; }
.stat .label { font-size: 8.5pt; color: var(--secondary); margin-top: 4px; }
.note { font-size: 8.5pt; color: var(--muted); margin-top: 8px; }
header { margin-bottom: 8px; }
.account { break-before: page; }  /* each account starts on its own page */
@media screen { .account { margin-top: 40px; padding-top: 28px; border-top: 1px solid var(--border); } }
.timeline { list-style: none; padding: 0; margin: 0; }
.timeline li { display: flex; gap: 12px; padding: 6px 0; border-bottom: 1px solid #EDEDED; break-inside: avoid; }
.timeline .date { flex: none; width: 52px; font-family: var(--mono); font-size: 7.5pt; letter-spacing: 1px;
  text-transform: uppercase; color: var(--ink-soft); padding-top: 2px; }
.timeline .text { flex: 1; }
.sources { font-family: var(--mono); font-size: 7pt; color: var(--muted); white-space: nowrap; }
.sources a { color: var(--ink-soft); text-decoration: none; border: 1px solid var(--border); border-radius: 999px;
  padding: 0 4px; margin-left: 2px; }
.cards { margin-top: 14px; }
.card { background: var(--offwhite); border-radius: 12px; padding: 4px 14px 10px; margin-bottom: 10px; }
.card h3 { break-after: avoid; }
.card h3 { margin-top: 10px; }
.card ul { margin: 0; padding-left: 16px; }
.card li { margin-bottom: 4px; break-inside: avoid; }
table.follow-ups { width: 100%; border-collapse: collapse; font-size: 9pt; }
.follow-ups th { text-align: left; font-family: var(--mono); font-size: 7pt; letter-spacing: 1px;
  text-transform: uppercase; color: var(--ink-soft); font-weight: 400; padding: 4px 6px 4px 0;
  border-bottom: 1px solid var(--border); }
.follow-ups td { padding: 5px 6px 5px 0; border-bottom: 1px solid #EDEDED; vertical-align: top; }
.follow-ups tr { break-inside: avoid; }
.follow-ups a { text-decoration: none; }
.nowrap { white-space: nowrap; }
.muted { color: var(--muted); font-size: 8.5pt; margin-top: 6px; }
footer { margin-top: 28px; font-family: var(--mono); font-size: 7.5pt; color: var(--muted); }
"""
    )
