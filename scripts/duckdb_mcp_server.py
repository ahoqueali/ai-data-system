#!/usr/bin/env python3
"""
DuckDB MCP server — exposes a run_query tool over stdio (JSON-RPC 2.0).

Env vars:
    DUCKDB_PATH  — path to the .duckdb database file (required)

On first connect, if the database has no tables, auto-seeds from
generated_ddl/*.sql (BigQuery DDL is converted to DuckDB-compatible SQL).

Protocol: MCP over stdio (initialize → tools/list → tools/call)
"""

import json
import os
import re
import sys
from pathlib import Path

try:
    import duckdb
except ImportError:
    sys.exit("Missing dependency. Run: pip install duckdb")

ROOT = Path(__file__).resolve().parent.parent
DDL_DIR = ROOT / "generated_ddl"

# ---------------------------------------------------------------------------
# BigQuery → DuckDB DDL conversion
# ---------------------------------------------------------------------------

_TYPE_MAP = {
    "STRING": "VARCHAR",
    "NUMERIC": "DECIMAL(38, 9)",
    "BIGNUMERIC": "DECIMAL(76, 38)",
    "BOOL": "BOOLEAN",
    "INT64": "BIGINT",
    "FLOAT64": "DOUBLE",
    "BYTES": "BLOB",
    "DATE": "DATE",
    "DATETIME": "TIMESTAMP",
    "TIMESTAMP": "TIMESTAMPTZ",
    "TIME": "TIME",
    "GEOGRAPHY": "VARCHAR",
    "JSON": "JSON",
}


def _convert_type(t: str) -> str:
    return _TYPE_MAP.get(t.upper(), t.upper())


def _strip_options(sql: str) -> str:
    """Remove all OPTIONS(...) blocks, handling nested parens in descriptions."""
    result = []
    i = 0
    while i < len(sql):
        match = re.search(r'OPTIONS\s*\(', sql[i:], re.IGNORECASE)
        if not match:
            result.append(sql[i:])
            break
        result.append(sql[i : i + match.start()])
        # Find matching closing paren
        start = i + match.start() + match.end() - match.start()  # after OPTIONS(
        depth = 1
        j = start
        while j < len(sql) and depth > 0:
            if sql[j] == "(":
                depth += 1
            elif sql[j] == ")":
                depth -= 1
            j += 1
        i = j
    return "".join(result)


def _strip_backticks(name: str) -> str:
    return name.replace("`", "")


def _table_name_from_backtick_ref(s: str) -> str:
    """Extract 'dataset.table' from `project.dataset.table`."""
    s = _strip_backticks(s)
    parts = s.split(".")
    if len(parts) >= 3:
        return ".".join(parts[-2:])
    return s


def convert_bigquery_ddl(ddl: str) -> str:
    """Convert a BigQuery CREATE TABLE statement to DuckDB-compatible SQL."""
    # Find table name
    m = re.search(r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?`([^`]+)`", ddl, re.IGNORECASE)
    if not m:
        return ddl
    table_ref = _table_name_from_backtick_ref(m.group(1))

    # Extract everything between the first ( after CREATE TABLE and its matching )
    open_paren = ddl.index("(", m.end())
    depth = 0
    close_paren = -1
    for i in range(open_paren, len(ddl)):
        if ddl[i] == "(":
            depth += 1
        elif ddl[i] == ")":
            depth -= 1
            if depth == 0:
                close_paren = i
                break
    if close_paren == -1:
        return ddl

    col_block = ddl[open_paren + 1 : close_paren].strip()

    # Strip OPTIONS(...) — these contain nested parens that would confuse splitting
    col_block = _strip_options(col_block)

    # Split on commas at top level
    columns = []
    current = ""
    depth = 0
    for ch in col_block:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        elif ch == "," and depth == 0:
            columns.append(current.strip())
            current = ""
            continue
        current += ch
    if current.strip():
        columns.append(current.strip())

    col_defs = []
    for col in columns:
        col = col.strip()
        if not col:
            continue
        tokens = col.split(None, 2)
        if len(tokens) >= 2:
            col_name = tokens[0]
            col_type = _convert_type(tokens[1])
            rest = ""
            if len(tokens) >= 3:
                rest = tokens[2]
            col_defs.append(f"  {col_name} {col_type} {rest}".rstrip())
        else:
            col_defs.append(f"  {col}")

    cols_sql = ",\n".join(col_defs)
    return f"CREATE TABLE IF NOT EXISTS {table_ref} (\n{cols_sql}\n);"


def _seed_database(con: duckdb.DuckDBPyConnection) -> None:
    """Run converted DDL from generated_ddl/ if the database is empty."""
    if not DDL_DIR.exists():
        return

    tables = con.execute(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_schema = 'main'"
    ).fetchall()
    if tables:
        return

    sql_files = sorted(DDL_DIR.glob("*.sql"))
    for sql_file in sql_files:
        raw = sql_file.read_text()
        converted = convert_bigquery_ddl(raw)
        try:
            con.execute(converted)
        except Exception as exc:
            print(f"[seed] Warning: failed to execute {sql_file.name}: {exc}",
                  file=sys.stderr)


# ---------------------------------------------------------------------------
# MCP protocol helpers
# ---------------------------------------------------------------------------

def _send(obj: dict) -> None:
    """Write a JSON-RPC message to stdout."""
    data = json.dumps(obj)
    sys.stdout.write(f"Content-Length: {len(data.encode())}\r\n\r\n{data}")
    sys.stdout.flush()


def _send_result(req_id, result: dict) -> None:
    _send({"jsonrpc": "2.0", "id": req_id, "result": result})


def _send_error(req_id, code: int, message: str) -> None:
    _send({"jsonrpc": "2.0", "id": req_id,
           "error": {"code": code, "message": message}})


def _handle_initialize(req_id, _params):
    _send_result(req_id, {
        "protocolVersion": "2024-11-05",
        "capabilities": {"tools": {"listChanged": False}},
        "serverInfo": {"name": "duckdb", "version": "0.1.0"},
    })


def _handle_tools_list(req_id, _params):
    _send_result(req_id, {
        "tools": [
            {
                "name": "run_query",
                "description": (
                    "Execute a SQL query against the DuckDB database and return "
                    "results as JSON rows with column headers."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "sql": {
                            "type": "string",
                            "description": "The SQL query to execute",
                        },
                        "max_results": {
                            "type": "integer",
                            "description": "Maximum rows to return (default 100)",
                            "default": 100,
                        },
                    },
                    "required": ["sql"],
                },
            }
        ]
    })


def _handle_tools_call(req_id, params, con):
    tool_name = params.get("name")
    args = params.get("arguments", {})

    if tool_name == "run_query":
        sql = args.get("sql", "").strip()
        max_results = args.get("max_results", 100)
        if not sql:
            _send_error(req_id, -32602, "Missing required argument: sql")
            return
        try:
            result = con.execute(sql)
            columns = [desc[0] for desc in result.description] if result.description else []
            rows = result.fetchmany(max_results + 1)
            truncated = len(rows) > max_results
            rows = rows[:max_results]

            data = {
                "columns": columns,
                "rows": rows,
                "row_count": len(rows),
                "truncated": truncated,
            }
            _send_result(req_id, {
                "content": [{"type": "text", "text": json.dumps(data, default=str)}]
            })
        except Exception as exc:
            _send_error(req_id, -32000, str(exc))
    else:
        _send_error(req_id, -32601, f"Unknown tool: {tool_name}")


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def main():
    db_path = os.environ.get("DUCKDB_PATH")
    if not db_path:
        sys.exit("DUCKDB_PATH env var is required")

    con = duckdb.connect(db_path)
    _seed_database(con)

    # Read length-prefixed JSON-RPC messages from stdin
    buffer = b""
    while True:
        chunk = sys.stdin.buffer.read(4096)
        if not chunk:
            break
        buffer += chunk

        while True:
            # Look for Content-Length header
            header_end = buffer.find(b"\r\n\r\n")
            if header_end == -1:
                break

            header = buffer[:header_end].decode()
            length = 0
            for line in header.split("\r\n"):
                if line.lower().startswith("content-length:"):
                    length = int(line.split(":", 1)[1].strip())

            if length == 0:
                # Skip malformed message
                buffer = buffer[header_end + 4:]
                continue

            body_start = header_end + 4
            if len(buffer) < body_start + length:
                break  # Wait for more data

            body = buffer[body_start:body_start + length]
            buffer = buffer[body_start + length:]

            try:
                msg = json.loads(body)
            except json.JSONDecodeError:
                continue

            method = msg.get("method")
            req_id = msg.get("id")
            params = msg.get("params", {})

            if method == "initialize":
                _handle_initialize(req_id, params)
            elif method == "notifications/initialized":
                pass  # No response needed for notifications
            elif method == "tools/list":
                _handle_tools_list(req_id, params)
            elif method == "tools/call":
                _handle_tools_call(req_id, params, con)
            elif method == "ping":
                _send_result(req_id, {})
            else:
                if req_id is not None:
                    _send_error(req_id, -32601, f"Method not found: {method}")

    con.close()


if __name__ == "__main__":
    main()
