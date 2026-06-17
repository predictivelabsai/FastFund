#!/usr/bin/env bash
# Run a local, user-owned Neo4j for SFO Hub dev (mirrors the TaxHub helper).
# Usage: scripts/neo4j_local.sh {setup|start|stop}
# In production prefer Neo4j AuraDB (set NEO4J_URI=neo4j+s://<id>.databases.neo4j.io).
set -euo pipefail

PASS="${NEO4J_PASSWORD:-sfohub-dev-password}"

case "${1:-}" in
  setup)
    docker pull neo4j:5.26-community
    echo "Pulled neo4j:5.26-community. Run: scripts/neo4j_local.sh start"
    ;;
  start)
    docker run -d --name sfohub-neo4j \
      -p 7474:7474 -p 7687:7687 \
      -e NEO4J_AUTH="neo4j/${PASS}" \
      -v sfohub-neo4j-data:/data \
      neo4j:5.26-community
    echo "Neo4j up — browser http://localhost:7474  bolt://localhost:7687  (neo4j/${PASS})"
    ;;
  stop)
    docker rm -f sfohub-neo4j 2>/dev/null || true
    ;;
  *)
    echo "Usage: $0 {setup|start|stop}"; exit 1;;
esac
