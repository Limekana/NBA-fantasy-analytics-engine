"""draft_board.html - a sortable, filterable board for draft day.

Handoff sec.26 asks for a readable interactive version sortable by model rank,
ADP, value gap, Lock-In value, ceiling and risk. Deliberately a single
self-contained file with no build step and no external assets: on draft day it
has to open instantly from a phone or a laptop with no network.
"""
from __future__ import annotations

import html
import json
from pathlib import Path

import numpy as np
import pandas as pd

DISPLAY_COLUMNS = [
    ("model_rank", "Rank", "int"),
    ("player_name", "Player", "text"),
    ("team", "Tm", "text"),
    ("position", "Pos", "text"),
    ("tier", "Tier", "text"),
    ("projected_fp_game", "FP/G", "num"),
    ("projected_games", "GP", "num"),
    ("projected_season_value", "Season", "num"),
    ("lockin_value", "LockIn", "num"),
    ("lock_in_advantage", "LI+", "num"),
    ("games_per_week", "G/Wk", "num"),
    ("median_fp", "Med", "num"),
    ("floor", "Floor", "num"),
    ("ceiling", "Ceil", "num"),
    ("std_dev", "SD", "num"),
    ("double_double_rate", "DD%", "pct"),
    ("triple_double_rate", "TD%", "pct"),
    ("40_point_rate", "40+%", "pct"),
    ("50_point_rate", "50+%", "pct"),
    ("15_assist_rate", "15A%", "pct"),
    ("20_rebound_rate", "20R%", "pct"),
    ("adp", "ADP", "num"),
    ("adp_vs_model", "Gap", "num"),
    ("value_flag", "Market", "text"),
    ("risk", "Risk", "num"),
    ("archetype", "Archetype", "text"),
]

_TEMPLATE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title>
<style>
  :root {{ color-scheme: light dark; --bg:#ffffff; --fg:#16191d; --muted:#5b6470;
    --line:#e3e6ea; --head:#f5f6f8; --accent:#1a56db; --good:#0f7b3d; --bad:#b3261e;
    --warn:#8a5a00; --warnbg:#fff4d6; }}
  @media (prefers-color-scheme: dark) {{ :root {{ --bg:#12151a; --fg:#e6e9ee;
    --muted:#98a2b3; --line:#242a33; --head:#1a1f27; --accent:#7aa2f7; --good:#4ade80;
    --bad:#f87171; --warn:#fbbf24; --warnbg:#3a2f10; }} }}
  * {{ box-sizing:border-box; }}
  body {{ margin:0; padding:16px; background:var(--bg); color:var(--fg);
    font:13px/1.45 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif; }}
  h1 {{ font-size:19px; margin:0 0 2px; }}
  .sub {{ color:var(--muted); font-size:12px; margin-bottom:12px; }}
  .warn {{ background:var(--warnbg); color:var(--warn); border:1px solid var(--warn);
    border-radius:6px; padding:10px 12px; margin-bottom:12px; font-weight:600; }}
  .controls {{ display:flex; gap:8px; flex-wrap:wrap; margin-bottom:10px; }}
  input,select {{ padding:6px 9px; border:1px solid var(--line); border-radius:6px;
    background:var(--bg); color:var(--fg); font-size:13px; }}
  input[type=search] {{ min-width:190px; }}
  .wrap {{ overflow-x:auto; border:1px solid var(--line); border-radius:8px; }}
  table {{ border-collapse:collapse; width:100%; font-variant-numeric:tabular-nums; }}
  th,td {{ padding:5px 8px; text-align:right; border-bottom:1px solid var(--line);
    white-space:nowrap; }}
  th {{ background:var(--head); position:sticky; top:0; cursor:pointer;
    user-select:none; font-size:11px; text-transform:uppercase; letter-spacing:.03em; }}
  th:hover {{ color:var(--accent); }}
  th.sorted::after {{ content:" \\2193"; }}
  th.sorted.asc::after {{ content:" \\2191"; }}
  td:nth-child(2),th:nth-child(2) {{ text-align:left; font-weight:600; }}
  td:nth-child(3),td:nth-child(4),td:nth-child(5),
  th:nth-child(3),th:nth-child(4),th:nth-child(5) {{ text-align:left; }}
  tbody tr:hover {{ background:var(--head); }}
  .undervalued {{ color:var(--good); font-weight:600; }}
  .overvalued {{ color:var(--bad); }}
  .tier-sep td {{ border-bottom:2px solid var(--accent); }}
  footer {{ color:var(--muted); font-size:11px; margin-top:14px; line-height:1.6; }}
</style></head><body>
<h1>{title}</h1>
<div class="sub">{subtitle}</div>
{warning}
<div class="controls">
  <input type="search" id="q" placeholder="Filter player / team...">
  <select id="pos"><option value="">All positions</option>{position_options}</select>
  <select id="tier"><option value="">All tiers</option>{tier_options}</select>
  <select id="flag"><option value="">All market flags</option>{flag_options}</select>
</div>
<div class="wrap"><table id="board">
<thead><tr>{header}</tr></thead><tbody>{rows}</tbody></table></div>
<footer>{footer}</footer>
<script>
const table=document.getElementById('board');
const tbody=table.tBodies[0];
const rows=Array.from(tbody.rows);
let sortCol=0, sortAsc=true;
function val(tr,i){{const c=tr.cells[i];const d=c.dataset.v;return d===undefined?c.textContent:parseFloat(d);}}
Array.from(table.tHead.rows[0].cells).forEach((th,i)=>{{
  th.addEventListener('click',()=>{{
    sortAsc = (sortCol===i) ? !sortAsc : (th.dataset.type!=='num');
    sortCol=i;
    Array.from(table.tHead.rows[0].cells).forEach(h=>h.classList.remove('sorted','asc'));
    th.classList.add('sorted'); if(sortAsc) th.classList.add('asc');
    const sorted=rows.slice().sort((a,b)=>{{
      let x=val(a,i),y=val(b,i);
      if(typeof x==='string'||typeof y==='string'){{x=String(x);y=String(y);return sortAsc?x.localeCompare(y):y.localeCompare(x);}}
      if(isNaN(x))x=sortAsc?Infinity:-Infinity; if(isNaN(y))y=sortAsc?Infinity:-Infinity;
      return sortAsc?x-y:y-x;}});
    tbody.replaceChildren(...sorted); applyFilter();
  }});
}});
function applyFilter(){{
  const q=document.getElementById('q').value.toLowerCase();
  const pos=document.getElementById('pos').value;
  const tier=document.getElementById('tier').value;
  const flag=document.getElementById('flag').value;
  Array.from(tbody.rows).forEach(tr=>{{
    const ok=(!q||tr.dataset.search.includes(q))&&(!pos||tr.dataset.pos===pos)
      &&(!tier||tr.dataset.tier===tier)&&(!flag||tr.dataset.flag===flag);
    tr.style.display=ok?'':'none';
  }});
}}
['q','pos','tier','flag'].forEach(id=>{{
  const el=document.getElementById(id);
  el.addEventListener(el.tagName==='INPUT'?'input':'change',applyFilter);
}});
</script></body></html>"""


def _format(value, kind: str) -> str:
    if value is None or (isinstance(value, float) and (np.isnan(value) or pd.isna(value))):
        return "&mdash;"
    if kind == "pct":
        return f"{float(value) * 100:.1f}"
    if kind == "int":
        return f"{float(value):.0f}"
    if kind == "num":
        number = float(value)
        return f"{number:.0f}" if abs(number) >= 100 else f"{number:.1f}"
    return html.escape(str(value))


def render_draft_board(board: pd.DataFrame, output_path: Path | str, subtitle: str = "") -> Path:
    """Write the interactive draft board."""
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    columns = [(k, label, kind) for k, label, kind in DISPLAY_COLUMNS if k in board.columns]
    header = "".join(
        f'<th data-type="{"num" if kind in ("num", "int", "pct") else "text"}" '
        f'title="{html.escape(key)}">{html.escape(label)}</th>'
        for key, label, kind in columns
    )

    body: list[str] = []
    previous_tier = None
    for row in board.itertuples(index=False):
        tier = str(getattr(row, "tier", ""))
        flag = str(getattr(row, "value_flag", ""))
        classes = []
        if previous_tier is not None and tier != previous_tier:
            classes.append("tier-sep")
        previous_tier = tier
        search = f"{getattr(row, 'player_name', '')} {getattr(row, 'team', '')}".lower()

        cells = []
        for key, _label, kind in columns:
            value = getattr(row, key, None)
            text = _format(value, kind)
            cell_class = ""
            if key == "value_flag":
                cell_class = f' class="{html.escape(flag)}"'
            raw = "" if (value is None or (isinstance(value, float) and pd.isna(value))) else html.escape(str(value))
            cells.append(f'<td data-v="{raw}"{cell_class}>{text}</td>')

        body.append(
            f'<tr class="{" ".join(classes)}" data-search="{html.escape(search)}" '
            f'data-pos="{html.escape(str(getattr(row, "position", "")))}" '
            f'data-tier="{html.escape(tier)}" data-flag="{html.escape(flag)}">'
            + "".join(cells) + "</tr>"
        )

    def options(column: str) -> str:
        if column not in board.columns:
            return ""
        values = sorted({str(v) for v in board[column].dropna().unique()})
        return "".join(f'<option value="{html.escape(v)}">{html.escape(v)}</option>' for v in values)

    is_synthetic = bool(board["is_synthetic"].any()) if "is_synthetic" in board.columns else False
    warning = ""
    if is_synthetic:
        warning = (
            '<div class="warn">&#9888; SYNTHETIC DATA &mdash; these are generated '
            "players, not real NBA players. This board is a pipeline demonstration "
            "only. Do not draft from it. Ingest real game logs to produce a real board.</div>"
        )

    no_adp = ("adp" not in board.columns) or bool(board["adp"].isna().all())
    if no_adp:
        warning += (
            '<div class="warn">&#9888; NO ADP LOADED &mdash; market-value columns '
            "(ADP, Gap, Market) are empty, so this board cannot identify draft-day "
            "bargains. Add a source to config/sources.yaml.</div>"
        )

    footer = (
        "Click any column header to sort. LockIn = expected weekly Lock-In score; "
        "LI+ = advantage over raw FP/game; Gap = ADP minus model rank (positive means "
        "the market lets you wait). Risk combines availability, variance and role "
        "uncertainty. Rates are percentages of games played.<br>"
        "Projections are assumptions, not facts &mdash; see docs/assumptions.md."
    )

    html_text = _TEMPLATE.format(
        title="NBA Fantasy Draft Board 2026-27",
        subtitle=html.escape(subtitle),
        warning=warning,
        header=header,
        rows="\n".join(body),
        position_options=options("position"),
        tier_options=options("tier"),
        flag_options=options("value_flag"),
        footer=footer,
    )
    output_path.write_text(html_text, encoding="utf-8")
    return output_path
