from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pandas as pd


# Simple regex patterns for SQL table extraction.
# Supports:
# - optional quoting: `table`, "table", [table]
# - optional schema: schema.table
_SQL_IDENT = r'(?:`[^`]+`|"[^"]+"|\[[^\]]+\]|[a-zA-Z_][a-zA-Z0-9_\$]*)'
_SQL_QUALIFIED = rf'(?:{_SQL_IDENT}\.)?({_SQL_IDENT})'

SQL_TABLE_PATTERNS = [
    re.compile(rf'\bFROM\s+{_SQL_QUALIFIED}', re.IGNORECASE),
    re.compile(rf'\bJOIN\s+{_SQL_QUALIFIED}', re.IGNORECASE),
    re.compile(rf'\bUPDATE\s+{_SQL_QUALIFIED}', re.IGNORECASE),
    re.compile(rf'\bINTO\s+{_SQL_QUALIFIED}', re.IGNORECASE),
]


# Keywords to ignore (not table names)
IGNORE_KEYWORDS = {
    'select', 'insert', 'update', 'delete', 'where', 'and', 'or', 'set',
    'values', 'returning', 'order', 'group', 'by', 'having', 'limit',
    'offset', 'distinct', 'all', 'union', 'intersect', 'except'
}


def _is_db_span(row: pd.Series) -> bool:
    """Check if a span is a database operation span."""
    tags = _parse_tags(row.get("tags", ""))

    # Strong signals from OpenTelemetry/Jaeger semantic conventions
    if _get_tag_value(tags, "db.system"):
        return True
    if _get_tag_value(tags, "db.statement"):
        return True

    # Fallback: operation_name heuristic (MVP)
    operation = str(row.get("operation_name", "")).upper()
    sql_keywords = ["SELECT", "INSERT", "UPDATE", "DELETE", "FROM", "JOIN"]
    return any(keyword in operation for keyword in sql_keywords)


def _parse_tags(tags_str: Any) -> list[dict[str, Any]]:
    """Parse the flattened tags JSON (list of {key,value})."""
    if tags_str is None or (isinstance(tags_str, float) and pd.isna(tags_str)):
        return []
    if isinstance(tags_str, list):
        return tags_str
    if not isinstance(tags_str, str) or not tags_str:
        return []
    try:
        data = json.loads(tags_str)
        return data if isinstance(data, list) else []
    except (json.JSONDecodeError, TypeError):
        return []


def _get_tag_value(tags: list[dict[str, Any]], key: str) -> str | None:
    for tag in tags:
        if str(tag.get("key", "")) == key:
            value = tag.get("value")
            if value is None:
                return None
            return str(value)
    return None


def _unquote_sql_identifier(identifier: str) -> str:
    identifier = identifier.strip()
    if len(identifier) < 2:
        return identifier
    if (identifier.startswith("`") and identifier.endswith("`")) or (
        identifier.startswith('"') and identifier.endswith('"')
    ) or (identifier.startswith("[") and identifier.endswith("]")):
        return identifier[1:-1]
    return identifier


def _extract_tables_from_sql(sql: str) -> list[str]:
    """Extract table names from SQL statement using simple regex."""
    if not sql or not isinstance(sql, str):
        return []
    
    # Clean the SQL
    sql_clean = sql.replace("\n", " ").replace("\t", " ")
    
    tables = set()
    
    # Apply each pattern
    for pattern in SQL_TABLE_PATTERNS:
        matches = pattern.findall(sql_clean)
        for match in matches:
            table_name = _unquote_sql_identifier(match).strip().lower()
            # Ignore SQL keywords
            if table_name and table_name not in IGNORE_KEYWORDS:
                tables.add(table_name)
    
    return sorted(tables)


def _detect_db_system(operation_name: str) -> str:
    """Detect database system from operation name."""
    op_upper = operation_name.upper()
    
    if 'MONGODB' in op_upper or 'MONGO' in op_upper:
        return 'mongodb'
    if 'POSTGRES' in op_upper or 'POSTGRESQL' in op_upper:
        return 'postgresql'
    if 'MYSQL' in op_upper:
        return 'mysql'
    if 'SQLITE' in op_upper:
        return 'sqlite'
    
    # Default: assume SQL
    return 'sql'


def extract_db_operations(spans_df: pd.DataFrame) -> pd.DataFrame:
    """Extract database operations from spans DataFrame.
    
    Input columns: trace_id, span_id, parent_span_id, service_name, operation_name, start_time, duration
    Output columns: trace_id, span_id, service_name, db_system, db_statement, tables
    """
    if spans_df.empty:
        return pd.DataFrame(columns=[
            "trace_id", "span_id", "service_name", "db_system", "db_statement", "tables"
        ])
    
    # Find DB spans
    is_db = spans_df.apply(_is_db_span, axis=1)
    db_spans = spans_df[is_db].copy()
    
    if db_spans.empty:
        return pd.DataFrame(columns=[
            "trace_id", "span_id", "service_name", "db_system", "db_statement", "tables"
        ])
    
    # Parse tags once (stringified JSON list)
    db_spans["_tags_parsed"] = db_spans["tags"].apply(_parse_tags)

    # Detect DB system (prefer semantic tag)
    def detect_system(row: pd.Series) -> str:
        tags = row.get("_tags_parsed", [])
        system = _get_tag_value(tags, "db.system")
        if system:
            return system.lower()
        return _detect_db_system(str(row.get("operation_name", "")))

    db_spans["db_system"] = db_spans.apply(detect_system, axis=1)

    # Statement/query (prefer semantic tag)
    def detect_statement(row: pd.Series) -> str:
        tags = row.get("_tags_parsed", [])
        stmt = _get_tag_value(tags, "db.statement")
        if stmt:
            return stmt
        # Some instrumentations may use different keys
        alt = _get_tag_value(tags, "db.query")
        if alt:
            return alt
        return str(row.get("operation_name", ""))

    db_spans["db_statement"] = db_spans.apply(detect_statement, axis=1)

    # Extract tables
    db_spans["tables"] = db_spans["db_statement"].apply(_extract_tables_from_sql)
    
    # Convert tables list to comma-separated string
    db_spans["tables"] = db_spans["tables"].apply(lambda x: ",".join(x) if x else "")
    
    # Select columns
    result = db_spans[[
        "trace_id",
        "span_id",
        "service_name",
        "db_system",
        "db_statement",
        "tables",
    ]].copy()
    
    return result


def save_db_operations_csv(df: pd.DataFrame, output_path: Path) -> None:
    """Save DB operations DataFrame to CSV."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(output_path, index=False)
