"""Information Architecture analysis: URL hierarchy, depth metrics,
taxonomy/category breakdown, click-depth (via link graph), and orphan pages.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from urllib.parse import urlparse

import networkx as nx

from models import PageRecord


def build_link_graph(pages: dict[str, PageRecord], edges: list[tuple[str, str]]) -> nx.DiGraph:
    g = nx.DiGraph()
    for url in pages:
        g.add_node(url)
    for src, dst in edges:
        if dst in pages:
            g.add_edge(src, dst)
    return g


def click_depth(graph: nx.DiGraph, start_url: str) -> dict[str, int]:
    if start_url not in graph:
        return {}
    lengths = nx.single_source_shortest_path_length(graph, start_url)
    return lengths


def orphan_pages(graph: nx.DiGraph, start_url: str) -> list[str]:
    reachable = set(click_depth(graph, start_url).keys())
    return [n for n in graph.nodes if n not in reachable and n != start_url]


def url_taxonomy(pages: dict[str, PageRecord]) -> dict[str, int]:
    """Group pages by their first-level path segment as a rough taxonomy/category proxy."""
    counts: Counter[str] = Counter()
    for url in pages:
        path = urlparse(url).path
        segments = [s for s in path.split("/") if s]
        top = segments[0] if segments else "(home)"
        counts[top] += 1
    return dict(counts.most_common(30))


def build_hierarchy_tree(pages: dict[str, PageRecord]) -> dict:
    """Nested dict representing URL path hierarchy, for tree/sunburst visualization."""
    root = {"name": "/", "children": {}, "count": 0}
    for url in pages:
        path = urlparse(url).path
        segments = [s for s in path.split("/") if s] or ["(home)"]
        node = root
        node["count"] += 1
        for seg in segments:
            node = node["children"].setdefault(seg, {"name": seg, "children": {}, "count": 0})
            node["count"] += 1

    def to_list(node):
        return {
            "name": node["name"],
            "count": node["count"],
            "children": [to_list(c) for c in node["children"].values()],
        }

    return to_list(root)


def depth_distribution(pages: dict[str, PageRecord]) -> dict[int, int]:
    dist: Counter[int] = Counter()
    for rec in pages.values():
        dist[rec.path_depth] += 1
    return dict(sorted(dist.items()))


def run_ia_analysis(pages: dict[str, PageRecord], edges: list[tuple[str, str]], start_url: str) -> dict:
    graph = build_link_graph(pages, edges)
    depths = click_depth(graph, start_url)
    for url, d in depths.items():
        if url in pages:
            pass  # path_depth already set at crawl time; click-depth kept separately below

    orphans = orphan_pages(graph, start_url)
    max_click_depth = max(depths.values()) if depths else 0
    avg_click_depth = round(sum(depths.values()) / len(depths), 2) if depths else 0

    return {
        "click_depths": depths,
        "max_click_depth": max_click_depth,
        "avg_click_depth": avg_click_depth,
        "orphan_pages": orphans,
        "orphan_page_count": len(orphans),
        "taxonomy": url_taxonomy(pages),
        "hierarchy_tree": build_hierarchy_tree(pages),
        "path_depth_distribution": depth_distribution(pages),
        "pages_over_3_clicks": sum(1 for d in depths.values() if d > 3),
        "graph_node_count": graph.number_of_nodes(),
        "graph_edge_count": graph.number_of_edges(),
    }
