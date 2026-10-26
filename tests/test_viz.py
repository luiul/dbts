from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta

from dbts._viz import (
    COLOR_EDGE,
    COLOR_EDGE_BROKEN,
    COLOR_GREEN,
    COLOR_GREY,
    COLOR_RED,
    Dag,
    build_dag,
    build_freshness_graph,
    build_plan_graph,
    cost_color,
    cost_size,
    hierarchy_levels,
    name_from_unique_id,
    render_html,
    write_html,
)
from dbts.cost import ModelStats
from dbts.freshness import TableInfo

# ----- helpers ------------------------------------------------------------- #


def _model(
    name: str,
    parents: list[str] | None = None,
    project: str = "proj",
    database: str = "DB",
    schema: str = "S",
    materialized: str = "table",
    tags: list[str] | None = None,
) -> dict:
    return {
        "name": name,
        "depends_on": {"nodes": [f"model.{project}.{p}" for p in (parents or [])]},
        "config": {"database": database, "schema": schema, "materialized": materialized},
        "tags": tags or [],
    }


def _stat(name: str, p50: float, mode: str = "incremental", warehouse: str = "MEDIUM") -> ModelStats:
    return ModelStats(
        model=name,
        mode=mode,
        run_count=10,
        p50_elapsed_s=p50,
        last_seen=datetime.now(tz=UTC),
        warehouse_size=warehouse,
    )


# ----- name_from_unique_id ------------------------------------------------- #


def test_name_from_unique_id_extracts_model_name():
    assert name_from_unique_id("model.proj.fct_orders") == "fct_orders"


def test_name_from_unique_id_returns_none_for_source():
    assert name_from_unique_id("source.proj.raw.events") is None


# ----- build_dag ----------------------------------------------------------- #


def test_build_dag_topology_diamond():
    records = [
        _model("a"),
        _model("b", ["a"]),
        _model("c", ["a"]),
        _model("d", ["b", "c"]),
    ]
    dag = build_dag(records)
    assert isinstance(dag, Dag)
    assert set(dag.by_name) == {"a", "b", "c", "d"}
    assert sorted(dag.children["a"]) == ["b", "c"]
    assert sorted(dag.children["b"]) == ["d"]
    assert sorted(dag.parents["d"]) == ["b", "c"]


def test_build_dag_drops_external_parents():
    records = [_model("a", ["external_source"]), _model("b", ["a"])]
    dag = build_dag(records)
    assert dag.parents.get("a", []) == []
    assert dag.parents["b"] == ["a"]


def test_build_dag_drops_self_loop():
    # Pathological but should not crash or self-edge.
    records = [{"name": "x", "depends_on": {"nodes": ["model.proj.x"]}}]
    dag = build_dag(records)
    assert dag.children == {}
    assert dag.parents == {}


def test_hierarchy_levels_assigns_longest_path():
    records = [_model("a"), _model("b", ["a"]), _model("c", ["b"]), _model("d", ["a"])]
    levels = hierarchy_levels(build_dag(records))
    assert levels["a"] == 0
    assert levels["b"] == 1
    assert levels["c"] == 2
    assert levels["d"] == 1


# ----- color helpers ------------------------------------------------------- #


def test_cost_color_endpoints():
    assert cost_color(0.0).lower() == COLOR_GREEN.lower()
    assert cost_color(1.0).lower() == COLOR_RED.lower()


def test_cost_color_midpoint_yellow_ish():
    # At t=0.5 we should be exactly on the yellow anchor.
    assert cost_color(0.5).lower() == "#f59e0b"


def test_cost_size_monotonic():
    assert cost_size(0.0) < cost_size(0.5) < cost_size(1.0)


# ----- plan graph ---------------------------------------------------------- #


def test_build_plan_graph_neutral_when_no_stats():
    records = [_model("a"), _model("b", ["a"])]
    nodes, edges = build_plan_graph(records, stats_by_model={})
    by_id = {n.id: n for n in nodes}
    assert by_id["a"].color == COLOR_GREY
    assert by_id["b"].color == COLOR_GREY
    assert {(e.source, e.target, e.color) for e in edges} == {("a", "b", COLOR_EDGE)}


def test_build_plan_graph_colors_by_p50():
    records = [_model("cheap"), _model("mid"), _model("hot")]
    stats = {
        "cheap": {"incremental": _stat("cheap", 1.0)},
        "mid": {"incremental": _stat("mid", 50.0)},
        "hot": {"incremental": _stat("hot", 100.0)},
    }
    nodes, _ = build_plan_graph(records, stats_by_model=stats)
    by_id = {n.id: n for n in nodes}
    # Cheapest node should get the green anchor; hottest the red anchor.
    assert by_id["hot"].color.lower() == COLOR_RED.lower()
    # Sizes scale with p50.
    assert by_id["cheap"].size < by_id["mid"].size < by_id["hot"].size


def test_build_plan_graph_tooltip_contains_credits():
    records = [_model("hot")]
    stats = {"hot": {"incremental": _stat("hot", 600.0, warehouse="MEDIUM")}}
    nodes, _ = build_plan_graph(records, stats_by_model=stats)
    title = nodes[0].title
    assert "credits" in title
    assert "median run" in title


def test_build_plan_graph_label_html_with_cost():
    records = [_model("hot", materialized="incremental")]
    stats = {"hot": {"incremental": _stat("hot", 45.0, warehouse="MEDIUM")}}
    nodes, _ = build_plan_graph(records, stats_by_model=stats)
    label = nodes[0].label_html
    # Bold name line + "materialization · runtime" + "~credits cr".
    assert "<b>hot</b>" in label
    assert "incremental · 45.0s" in label
    assert "cr" in label
    assert label.count("\n") == 2


def test_build_plan_graph_label_html_without_cost():
    nodes, _ = build_plan_graph([_model("a", materialized="view")], stats_by_model={})
    assert nodes[0].label_html == "<b>a</b>\nview"


# ----- freshness graph ----------------------------------------------------- #


def _info(name: str, last_altered: datetime | None, rows: int | None = 100) -> TableInfo:
    return TableInfo(database="DB", schema="S", name=name.upper(), last_altered=last_altered, row_count=rows)


def test_build_freshness_graph_color_buckets():
    now = datetime(2026, 5, 27, 12, 0, tzinfo=UTC)
    threshold = now - timedelta(hours=6)
    records = [_model("fresh"), _model("stale"), _model("missing")]
    info_by_table = {
        ("DB", "S", "FRESH"): _info("fresh", now - timedelta(minutes=30)),
        ("DB", "S", "STALE"): _info("stale", now - timedelta(days=2)),
        # missing has no info entry → no_data grey
    }
    nodes, _ = build_freshness_graph(records, info_by_table, threshold, target_name="live", sandbox_user=None)
    by_id = {n.id: n for n in nodes}
    assert by_id["fresh"].color == COLOR_GREEN
    assert by_id["stale"].color == COLOR_RED
    assert by_id["missing"].color == COLOR_GREY


def test_build_freshness_graph_label_html_per_state():
    now = datetime(2026, 5, 27, 12, 0, tzinfo=UTC)
    threshold = now - timedelta(hours=6)
    records = [_model("fresh"), _model("stale"), _model("missing")]
    info_by_table = {
        ("DB", "S", "FRESH"): _info("fresh", now - timedelta(minutes=30), rows=1_200_000),
        ("DB", "S", "STALE"): _info("stale", now - timedelta(days=2), rows=500),
    }
    nodes, _ = build_freshness_graph(records, info_by_table, threshold, target_name="live", sandbox_user=None)
    by_id = {n.id: n for n in nodes}
    assert by_id["fresh"].label_html.startswith("<b>fresh</b>\n")
    assert "1.2M rows" in by_id["fresh"].label_html
    assert "stale · " in by_id["stale"].label_html
    assert "500 rows" in by_id["stale"].label_html
    # no_data node has no rows line.
    assert by_id["missing"].label_html == "<b>missing</b>\nno data"


def test_build_freshness_graph_marks_broken_chain_edges():
    now = datetime(2026, 5, 27, 12, 0, tzinfo=UTC)
    threshold = now - timedelta(hours=6)
    records = [_model("a"), _model("b", ["a"]), _model("c", ["b"])]
    info_by_table = {
        ("DB", "S", "A"): _info("a", now - timedelta(minutes=10)),  # fresh
        ("DB", "S", "B"): _info("b", now - timedelta(days=1)),  # stale
        ("DB", "S", "C"): _info("c", now - timedelta(days=2)),  # stale
    }
    _, edges = build_freshness_graph(records, info_by_table, threshold, target_name="live", sandbox_user=None)
    edge_by_pair = {(e.source, e.target): e.color for e in edges}
    # a→b: a is fresh, b is stale → normal edge.
    assert edge_by_pair[("a", "b")] == COLOR_EDGE
    # b→c: both stale → broken chain.
    assert edge_by_pair[("b", "c")] == COLOR_EDGE_BROKEN


# ----- render_html / write_html ------------------------------------------- #


def test_render_html_embeds_payload_with_nodes_and_edges():
    records = [_model("a"), _model("b", ["a"])]
    nodes, edges = build_plan_graph(records, stats_by_model={})
    out = render_html(nodes, edges, title="dbts plan", subtitle="2 models")
    assert "<title>dbts plan</title>" in out
    assert "vis-network" in out
    # Pull the JSON blob out of `const PAYLOAD = {...};`.
    match = re.search(r"const PAYLOAD = (\{.*?\});", out, flags=re.DOTALL)
    assert match is not None, "PAYLOAD JSON not found in HTML"
    payload = json.loads(match.group(1))
    assert {n["id"] for n in payload["nodes"]} == {"a", "b"}
    assert any(e["source"] == "a" and e["target"] == "b" for e in payload["edges"])


def test_write_html_creates_parent_dirs(tmp_path):
    nodes, edges = build_plan_graph([_model("solo")], stats_by_model={})
    out = render_html(nodes, edges, title="t", subtitle="")
    target = tmp_path / "nested" / "graph.html"
    written = write_html(out, target)
    assert written.exists()
    assert "solo" in written.read_text(encoding="utf-8")


def test_write_html_overwrites_silently(tmp_path):
    target = tmp_path / "graph.html"
    write_html("first", target)
    write_html("second", target)
    assert target.read_text(encoding="utf-8") == "second"
