#!/usr/bin/env python3
"""Load graph_v2.nq into Neo4j via the neosemantics (n10s) plugin.

    make neo4j-up                          # start neo4j (with n10s + apoc)
    uv run python -m src.domain_kg.load_neo4j

Needs the neo4j stack up (NEO4J_PLUGINS includes "n10s") and
`./src/domain_kg` mounted read-only at `/import/domain_kg` in the neo4j
container -- both set in docker-compose.yml.

n10s' RDF importer is triple-based, so named-graph structure (the
`urn:graph:inferred` split query.py cares about) does not survive a raw
n10s.rdf.import.fetch. This script therefore mirrors query.py's own default
view: it loads the SOURCE-asserted graph only (skips `urn:graph:inferred`)
by re-serializing to plain N-Triples first. Run with --include-inferred to
load everything, indistinguishably, if you want the derived triples too.
"""

from __future__ import annotations

import argparse
import sys

from neo4j import Driver

from src.student_kg.driver import make_driver

INFERRED_GRAPH = "inferred"
IMPORT_FILE = "file:///import/domain_kg/graph_v2.nt"


def _default_graph_to_ntriples(
    nq_path: str, nt_path: str, include_inferred: bool
) -> int:
    from rdflib import Dataset, Graph

    ds = Dataset()
    ds.parse(nq_path, format="nquads")
    g = Graph()
    for ctx in ds.contexts():
        cid = str(ctx.identifier)
        if not include_inferred and cid.endswith(INFERRED_GRAPH):
            continue
        for triple in ctx:
            g.add(triple)
    g.serialize(destination=nt_path, format="nt")
    return len(g)


def init_n10s(driver: Driver) -> None:
    with driver.session() as session:
        session.run(
            "CREATE CONSTRAINT n10s_unique_uri IF NOT EXISTS "
            "FOR (r:Resource) REQUIRE r.uri IS UNIQUE"
        )
        session.run(
            "CALL n10s.graphconfig.init({handleVocabUris: 'SHORTEN', "
            "handleMultival: 'OVERWRITE'})"
        )


def import_ntriples(driver: Driver, url: str) -> dict:
    with driver.session() as session:
        result = session.run("CALL n10s.rdf.import.fetch($url, 'N-Triples')", url=url)
        return dict(result.single())


def apply_node_typing(driver: Driver) -> None:
    """Collapse n10s' auto-minted rdf:type labels into the source's real
    2-kind model: `inst:` URIs are post-coordinated Instances, `sct:`/`rxn:`
    URIs are Concepts.

    n10s mints one Neo4j label per distinct `rdf:type` object it sees (a
    ClinicalAssertion, a FigureImage, a bare SNOMED/RXNORM id used as a type
    for a post-coordinated instance, ...), each under an arbitrary `nsN__`
    prefix. That is faithful to the RDF but not the mental model the graph
    was authored against, so replace it: tag every node by its own URI
    namespace and drop the auto-minted labels so the legend only ever shows
    Resource / Instance / Concept.
    """
    with driver.session() as session:
        session.run(
            "MATCH (r:Resource) WHERE r.uri STARTS WITH $inst " "SET r:Instance",
            inst="http://example.org/medkg/inst/",
        )
        session.run(
            "MATCH (r:Resource) WHERE r.uri STARTS WITH $sct "
            "OR r.uri STARTS WITH $rxn SET r:Concept",
            sct="http://snomed.info/id/",
            rxn="http://purl.bioontology.org/ontology/RXNORM/",
        )
        session.run(
            "MATCH (r:Resource) "
            "WITH r, [l IN labels(r) WHERE NOT l IN ['Resource', 'Instance', "
            "'Concept']] AS extra "
            "CALL apoc.create.removeLabels(r, extra) YIELD node "
            "RETURN count(*)"
        )


def rename_label_property(driver: Driver) -> None:
    """rdfs__label -> label.

    Neo4j Browser's default caption picks the alphabetically-first property
    on the node -- there is no "prefer name/label/title" heuristic. That put
    `ns0__mentionCount` ahead of `rdfs__label`, so nodes captioned as a
    number. `label` sorts before `ns0__...`, so this makes the browser
    default caption correct without a manual :style override.
    """
    with driver.session() as session:
        session.run(
            "MATCH (r:Resource) WHERE r.rdfs__label IS NOT NULL "
            "SET r.label = r.rdfs__label "
            "REMOVE r.rdfs__label"
        )


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--nq", default="src/domain_kg/graph_v2.nq")
    ap.add_argument(
        "--nt-out",
        default="src/domain_kg/graph_v2.nt",
        help="intermediate N-Triples file, written under the " "neo4j import mount",
    )
    ap.add_argument(
        "--include-inferred",
        action="store_true",
        help="also load urn:graph:inferred (Stage 7 derived triples)",
    )
    args = ap.parse_args(argv)

    n = _default_graph_to_ntriples(args.nq, args.nt_out, args.include_inferred)
    print(f"wrote {n} triples -> {args.nt_out}")

    driver = make_driver()
    try:
        init_n10s(driver)
        stats = import_ntriples(driver, IMPORT_FILE)
        print(f"imported: {stats}")
        apply_node_typing(driver)
        rename_label_property(driver)
    finally:
        driver.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
