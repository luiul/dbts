"""Graph visualization for `dbts plan` and `dbts freshness`.

Produces a self-contained HTML file that renders the build set as an
interactive DAG using vis-network (loaded via CDN). Shared DAG construction
lives here so both `plan.py` and `freshness.py` (and `topological_sort`)
agree on the edge set.
"""

from __future__ import annotations

import html
import json
import logging
import re
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from string import Template
from typing import Any

log = logging.getLogger("dbts.viz")


# ----- DAG construction (shared with topological_sort) ---------------------- #


@dataclass(frozen=True)
class Dag:
    """Build-set DAG keyed by model name."""

    by_name: dict[str, dict]
    children: dict[str, list[str]]
    parents: dict[str, list[str]]


def build_dag(records: Iterable[dict]) -> Dag:
    """Extract parent/child edges from `depends_on.nodes`, restricted to the build set.

    External parents (not in the build set) are dropped — they're effectively
    treated as already satisfied.
    """
    by_name: dict[str, dict] = {r["name"]: r for r in records if r.get("name")}
    children: dict[str, list[str]] = defaultdict(list)
    parents: dict[str, list[str]] = defaultdict(list)
    for name, r in by_name.items():
        for parent_uid in (r.get("depends_on") or {}).get("nodes") or []:
            parent_name = name_from_unique_id(parent_uid)
            if parent_name and parent_name in by_name and parent_name != name:
                children[parent_name].append(name)
                parents[name].append(parent_name)
    return Dag(by_name=by_name, children=dict(children), parents=dict(parents))


def name_from_unique_id(unique_id: str) -> str | None:
    """Extract the model name from `model.<project>.<name>` references."""
    m = re.match(r"^model\.[^.]+\.(.+)$", unique_id)
    return m.group(1) if m else None


def hierarchy_levels(dag: Dag) -> dict[str, int]:
    """Assign a depth level to each node = longest path from any root.

    Used by vis-network's hierarchical layout for stable top-down placement.
    """
    levels: dict[str, int] = {}
    # Iterate until fixed point — DAG so this terminates.
    changed = True
    while changed:
        changed = False
        for name in dag.by_name:
            ps = dag.parents.get(name) or []
            new_level = max((levels.get(p, 0) + 1 for p in ps), default=0)
            if levels.get(name) != new_level:
                levels[name] = new_level
                changed = True
    return levels


# ----- node/edge dataclasses ----------------------------------------------- #


@dataclass
class GraphNode:
    id: str
    label: str
    color: str
    size: int
    title: str
    level: int
    label_html: str = ""
    group: str = ""
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class GraphEdge:
    source: str
    target: str
    color: str = ""


# ----- color/size helpers --------------------------------------------------- #

# Diverging green → yellow → red scale used for both plan (p50 runtime) and
# freshness (staleness). Picked from the Tailwind palette so both views feel
# consistent without pulling in matplotlib.
COLOR_GREEN = "#10b981"
COLOR_YELLOW = "#f59e0b"
COLOR_RED = "#ef4444"
COLOR_GREY = "#9ca3af"
COLOR_EDGE = "rgba(203,213,225,0.55)"
COLOR_EDGE_BROKEN = "#ef4444"

NODE_SIZE_MIN = 14
NODE_SIZE_MAX = 48


def _lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * max(0.0, min(1.0, t))


def _hex_to_rgb(c: str) -> tuple[int, int, int]:
    c = c.lstrip("#")
    return int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)


def _rgb_to_hex(r: float, g: float, b: float) -> str:
    return f"#{round(r):02x}{round(g):02x}{round(b):02x}"


def _interp_color(c1: str, c2: str, t: float) -> str:
    r1, g1, b1 = _hex_to_rgb(c1)
    r2, g2, b2 = _hex_to_rgb(c2)
    return _rgb_to_hex(_lerp(r1, r2, t), _lerp(g1, g2, t), _lerp(b1, b2, t))


def cost_color(t: float) -> str:
    """0.0 = cheap (green) → 0.5 = medium (yellow) → 1.0 = expensive (red)."""
    if t <= 0.5:
        return _interp_color(COLOR_GREEN, COLOR_YELLOW, t * 2)
    return _interp_color(COLOR_YELLOW, COLOR_RED, (t - 0.5) * 2)


def cost_size(t: float) -> int:
    return round(_lerp(NODE_SIZE_MIN, NODE_SIZE_MAX, t))


# ----- plan graph ----------------------------------------------------------- #


def build_plan_graph(
    records: list[dict],
    stats_by_model: dict[str, dict[str, Any]],
    cost_report: Any | None = None,
) -> tuple[list[GraphNode], list[GraphEdge]]:
    """Nodes coloured by p50 runtime (red high → green low) when stats present.

    Falls back to neutral grey when `stats_by_model` is empty (no `--cost`).
    Size scales with relative p50 runtime across the build set.
    """
    # Late imports to avoid a viz → cost cycle in tooling.
    from dbts import cost as _cost

    dag = build_dag(records)
    levels = hierarchy_levels(dag)

    # Gather p50s for normalization.
    p50s: dict[str, float] = {}
    if stats_by_model:
        for name in dag.by_name:
            modes = stats_by_model.get(name) or {}
            stat = modes.get(_cost.MODE_INCREMENTAL) or modes.get(_cost.MODE_FULL_REFRESH)
            if stat is not None:
                p50s[name] = float(stat.p50_elapsed_s)

    p50_max = max(p50s.values()) if p50s else 0.0
    rate = _cost.credit_rate() if p50s else 0.0

    nodes: list[GraphNode] = []
    for name, record in dag.by_name.items():
        cfg = record.get("config") or {}
        materialization = str(cfg.get("materialized") or "?")
        tags = sorted(record.get("tags") or [])
        parents = dag.parents.get(name, [])
        name_esc = html.escape(name)

        modes = stats_by_model.get(name) or {}
        stat = modes.get(_cost.MODE_INCREMENTAL) or modes.get(_cost.MODE_FULL_REFRESH)

        if stat is not None and p50_max > 0:
            t = (p50s.get(name, 0.0)) / p50_max
            color = cost_color(t)
            size = cost_size(t)
            credits = _cost.credits_for(stat.p50_elapsed_s, stat.warehouse_size)
            usd = credits * rate
            now = datetime.now(tz=UTC)
            title_lines = [
                name,
                f"materialization: {materialization}",
                f"parents (in set): {len(parents)}",
                f"median run: {_cost.format_duration(stat.p50_elapsed_s)}",
                f"~{credits:.2f} credits  (~${usd:.2f}, warehouse {stat.warehouse_size})",
                f"last seen: {_cost.format_relative(stat.last_seen, now)}",
            ]
            if tags:
                title_lines.append(f"tags: {', '.join(tags)}")
            label_html = (
                f"<b>{name_esc}</b>\n{materialization} · {_cost.format_duration(stat.p50_elapsed_s)}\n~{credits:.2f} cr"
            )
        else:
            color = COLOR_GREY
            size = NODE_SIZE_MIN + 6
            title_lines = [
                name,
                f"materialization: {materialization}",
                f"parents (in set): {len(parents)}",
                "no cost data" if stats_by_model else "cost not requested (--cost)",
            ]
            if tags:
                title_lines.append(f"tags: {', '.join(tags)}")
            label_html = f"<b>{name_esc}</b>\n{materialization}"

        nodes.append(
            GraphNode(
                id=name,
                label=name,
                label_html=label_html,
                color=color,
                size=size,
                title="\n".join(title_lines),
                level=levels.get(name, 0),
                group=materialization,
            ),
        )

    edges: list[GraphEdge] = []
    for parent, kids in dag.children.items():
        for child in kids:
            edges.append(GraphEdge(source=parent, target=child, color=COLOR_EDGE))
    return nodes, edges


# ----- freshness graph ------------------------------------------------------ #


def build_freshness_graph(
    sorted_records: list[dict],
    info_by_table: dict[tuple[str, str, str], Any],
    threshold: datetime | None,
    target_name: str,
    sandbox_user: str | None,
) -> tuple[list[GraphNode], list[GraphEdge]]:
    """Nodes red/green/grey by staleness. Edges red when both endpoints are stale."""
    from dbts import cost as _cost
    from dbts.freshness import _physical  # local import to avoid cycle

    dag = build_dag(sorted_records)
    levels = hierarchy_levels(dag)

    # State per node: "fresh" | "stale" | "no_data"
    state: dict[str, str] = {}
    nodes: list[GraphNode] = []
    now = datetime.now(tz=UTC)

    for name, record in dag.by_name.items():
        triplet = _physical(record, target_name, sandbox_user)
        info = info_by_table.get(triplet)

        if info is None or info.last_altered is None:
            node_state = "no_data"
            color = COLOR_GREY
            ts_str = "(missing)"
            ago_str = "—"
            rows_str = "—"
        else:
            stale = threshold is not None and info.last_altered < threshold
            node_state = "stale" if stale else "fresh"
            color = COLOR_RED if stale else COLOR_GREEN
            ts_str = info.last_altered.strftime("%Y-%m-%d %H:%M UTC")
            ago_str = _cost.format_relative(info.last_altered, now)
            rows_str = f"{info.row_count:,}" if info.row_count is not None else "—"

        state[name] = node_state
        title_lines = [
            name,
            f"{triplet[0]}.{triplet[1]}",
            f"last altered: {ts_str}",
            f"ago: {ago_str}",
            f"rows: {rows_str}",
            f"status: {node_state}",
        ]
        label_lines = [f"<b>{html.escape(name)}</b>"]
        if node_state == "no_data":
            label_lines.append("no data")
        else:
            label_lines.append(f"stale · {ago_str}" if node_state == "stale" else ago_str)
            if info is not None and info.row_count is not None:
                label_lines.append(f"{_cost.format_count(info.row_count)} rows")
        nodes.append(
            GraphNode(
                id=name,
                label=name,
                label_html="\n".join(label_lines),
                color=color,
                size=NODE_SIZE_MIN + (8 if node_state == "stale" else 4),
                title="\n".join(title_lines),
                level=levels.get(name, 0),
                group=node_state,
            ),
        )

    edges: list[GraphEdge] = []
    for parent, kids in dag.children.items():
        for child in kids:
            broken = state.get(parent) == "stale" and state.get(child) == "stale"
            edges.append(
                GraphEdge(
                    source=parent,
                    target=child,
                    color=COLOR_EDGE_BROKEN if broken else COLOR_EDGE,
                ),
            )
    return nodes, edges


# ----- HTML rendering ------------------------------------------------------- #


_VIS_NETWORK_CDN = "https://unpkg.com/vis-network@9.1.9/standalone/umd/vis-network.min.js"


_HTML_TEMPLATE = Template(
    """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>$title_html</title>
  <script src="$cdn"></script>
  <style>
    :root {
      --bg: #0f172a;
      --panel: #111827;
      --fg: #e5e7eb;
      --muted: #9ca3af;
      --accent: #38bdf8;
    }
    html, body { margin: 0; padding: 0; height: 100%; background: var(--bg); color: var(--fg);
      font: 13px/1.45 -apple-system, "SF Pro Text", Segoe UI, Roboto, Helvetica, Arial, sans-serif; }
    #graph { position: absolute; inset: 0; }
    #header { position: absolute; top: 0; left: 0; right: 0; padding: 10px 14px; z-index: 2;
      background: linear-gradient(180deg, rgba(15,23,42,0.92) 0%, rgba(15,23,42,0) 100%);
      pointer-events: none; }
    #header h1 { margin: 0; font-size: 14px; font-weight: 600; letter-spacing: 0.02em; }
    #header .subtitle { margin-top: 2px; color: var(--muted); font-size: 12px; }
    #search { position: absolute; top: 12px; right: 14px; z-index: 3; pointer-events: auto; }
    #search input { background: var(--panel); color: var(--fg); border: 1px solid #1f2937;
      border-radius: 6px; padding: 6px 10px; width: 220px; outline: none; font: inherit; }
    #search input:focus { border-color: var(--accent); }
    #legend { position: absolute; bottom: 12px; left: 12px; z-index: 2;
      background: rgba(17,24,39,0.85); border: 1px solid #1f2937; border-radius: 8px;
      padding: 10px 12px; max-width: 280px; }
    #legend h2 { margin: 0 0 6px 0; font-size: 12px; font-weight: 600; color: var(--fg); }
    #legend .row { display: flex; align-items: center; gap: 8px; margin: 3px 0; color: var(--muted); }
    #legend .swatch { width: 12px; height: 12px; border-radius: 50%; display: inline-block;
      border: 1px solid rgba(255,255,255,0.15); }
    #legend .scale { display: flex; align-items: center; gap: 6px; margin-top: 4px; }
    #legend .scale .bar { height: 8px; width: 140px; border-radius: 4px;
      background: linear-gradient(90deg, #10b981 0%, #f59e0b 50%, #ef4444 100%); }
    #legend small { color: var(--muted); font-size: 11px; }
    .vis-tooltip { background: #111827 !important; color: #e5e7eb !important;
      border: 1px solid #1f2937 !important; font: 12px/1.4 monospace !important;
      white-space: pre !important; padding: 8px 10px !important; border-radius: 6px !important; }
  </style>
</head>
<body>
  <div id="header">
    <h1>$title_html</h1>
    <div class="subtitle">$subtitle_html</div>
  </div>
  <div id="search"><input id="q" type="search" placeholder="Filter by name…" autocomplete="off"></div>
  <div id="graph"></div>
  <div id="legend">$legend_html</div>
  <script>
    const PAYLOAD = $payload_json;
    const container = document.getElementById('graph');
    // Border width encodes the same signal that node size used to (cost / staleness intensity).
    const sizeRange = { min: Infinity, max: -Infinity };
    PAYLOAD.nodes.forEach(n => {
      if (n.size < sizeRange.min) sizeRange.min = n.size;
      if (n.size > sizeRange.max) sizeRange.max = n.size;
    });
    const sizeSpread = Math.max(1, sizeRange.max - sizeRange.min);
    const borderFor = s => 1 + Math.round(((s - sizeRange.min) / sizeSpread) * 4);

    // Multi-font node labels: bold name line + dimmer secondary fact lines.
    const NODE_FONT = { multi: 'html', size: 13, color: '#334155', face: 'monospace',
      bold: { color: '#0f172a', size: 16, face: 'monospace' },
      ital: { color: '#334155', size: 13, face: 'monospace' } };
    const NODE_FONT_DIM = { multi: 'html', size: 13, color: 'rgba(15,23,42,0.35)', face: 'monospace',
      bold: { color: 'rgba(15,23,42,0.45)', size: 16, face: 'monospace' },
      ital: { color: 'rgba(15,23,42,0.35)', size: 13, face: 'monospace' } };

    const nodes = new vis.DataSet(PAYLOAD.nodes.map(n => ({
      id: n.id,
      label: n.label_html || n.label,
      title: n.title,
      level: n.level,
      group: n.group,
      shape: 'box',
      borderWidth: borderFor(n.size),
      borderWidthSelected: borderFor(n.size) + 1,
      shapeProperties: { borderRadius: 6 },
      margin: { top: 10, bottom: 10, left: 14, right: 14 },
      color: { background: n.color, border: n.color,
               highlight: { background: n.color, border: '#38bdf8' } },
      font: NODE_FONT,
    })));
    const edges = new vis.DataSet(PAYLOAD.edges.map((e, i) => ({
      id: 'e' + i,
      from: e.source,
      to: e.target,
      color: { color: e.color || 'rgba(203,213,225,0.55)', highlight: '#38bdf8' },
      arrows: { to: { enabled: true, scaleFactor: 0.5 } },
      smooth: { enabled: true, type: 'cubicBezier', roundness: 0.4, forceDirection: 'horizontal' },
    })));
    const network = new vis.Network(container, { nodes, edges }, {
      layout: { hierarchical: { enabled: true, direction: 'LR', sortMethod: 'directed',
                                nodeSpacing: 120, levelSeparation: 480, blockShifting: true,
                                edgeMinimization: true, parentCentralization: true,
                                treeSpacing: 160 } },
      physics: { enabled: false },
      interaction: { hover: true, tooltipDelay: 120, navigationButtons: true, keyboard: true },
      edges: { width: 1 },
    });

    const q = document.getElementById('q');
    const baseColors = new Map(PAYLOAD.nodes.map(n => [n.id, n.color]));
    q.addEventListener('input', () => {
      const term = q.value.trim().toLowerCase();
      const updates = PAYLOAD.nodes.map(n => {
        const match = !term || n.id.toLowerCase().includes(term);
        const base = baseColors.get(n.id);
        return {
          id: n.id,
          color: match
            ? { background: base, border: base }
            : { background: 'rgba(75,85,99,0.25)', border: 'rgba(75,85,99,0.25)' },
          font: match ? NODE_FONT : NODE_FONT_DIM,
        };
      });
      nodes.update(updates);
    });
  </script>
</body>
</html>
""",
)


def _node_to_payload(n: GraphNode) -> dict:
    d = asdict(n)
    d.pop("extra", None)
    return d


def _edge_to_payload(e: GraphEdge) -> dict:
    return {"source": e.source, "target": e.target, "color": e.color}


def render_html(
    nodes: list[GraphNode],
    edges: list[GraphEdge],
    *,
    title: str,
    subtitle: str = "",
    legend_html: str = "",
) -> str:
    """Serialize nodes/edges as JSON inside a self-contained HTML shell."""
    payload = {
        "nodes": [_node_to_payload(n) for n in nodes],
        "edges": [_edge_to_payload(e) for e in edges],
    }
    return _HTML_TEMPLATE.safe_substitute(
        cdn=_VIS_NETWORK_CDN,
        title_html=html.escape(title),
        subtitle_html=html.escape(subtitle),
        legend_html=legend_html or _default_legend(),
        payload_json=json.dumps(payload, ensure_ascii=False),
    )


def _default_legend() -> str:
    return (
        "<h2>Legend</h2>"
        '<div class="row"><span class="swatch" style="background:#10b981"></span>fresh / cheap</div>'
        '<div class="row"><span class="swatch" style="background:#f59e0b"></span>medium</div>'
        '<div class="row"><span class="swatch" style="background:#ef4444"></span>stale / expensive</div>'
        '<div class="row"><span class="swatch" style="background:#9ca3af"></span>no data</div>'
        '<div class="scale"><div class="bar"></div></div>'
        "<small>Nodes show name · materialization · runtime (plan) or staleness · rows (freshness). "
        "Border thickness scales with intensity. Type to filter. Drag to pan, scroll to zoom.</small>"
    )


def write_html(content: str, path: Path) -> Path:
    """Write HTML to `path`, overwriting silently. Returns the absolute path."""
    path = Path(path).expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def open_in_browser(path: Path) -> bool:
    """Open `path` in the default browser. Returns True on success, False otherwise."""
    import webbrowser

    try:
        return webbrowser.open(path.as_uri())
    except Exception:  # headless / no browser — caller still printed the path
        return False
