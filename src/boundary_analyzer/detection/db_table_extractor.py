from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pandas as pd


# Simple regex patterns for SQL table extraction
SQL_TABLE_PATTERNS = [
    # FROM table_name
    re.compile(r'\bFROM\s+([a-zA-Z_][a-zA-Z0-9_]*)', re.IGNORECASE),
    # JOIN table_name
    re.compile(r'\bJOIN\s+([a-zA-Z_][a-zA-Z0-9_]*)', re.IGNORECASE),
    # UPDATE table_name
    re.compile(r'\bUPDATE\s+([a-zA-Z_][a-zA-Z0-9_]*)', re.IGNORECASE),
    # INTO table_name
    re.compile(r'\bINTO\s+([a-zA-Z_][a-zA-Z0-9_]*)', re.IGNORECASE),
]


# Keywords to ignore (not table names)
IGNORE_KEYWORDS = {
    'select', 'insert', 'update', 'delete', 'where', 'and', 'or', 'set',
    'values', 'returning', 'order', 'group', 'by', 'having', 'limit',
    'offset', 'distinct', 'all', 'union', 'intersect', 'except'
}


def _is_db_span(row: pd.Series) -> bool:
    """Check if a span is a database operation span."""
    operation = str(row.get("operation_name", "")).upper()
    
    # Check for SQL keywords in operation name
    sql_keywords = ['SELECT', 'INSERT', 'UPDATE', 'DELETE', 'FROM', 'JOIN']
    for keyword in sql_keywords:
        if keyword in operation:
            return True
    
    return False


def _extract_tables_from_sql(sql: str) -> list[str]:
    """Extract table names from SQL statement using simple regex."""
    if not sql or not isinstance(sql, str):
        return []
    
    # Clean the SQL
    sql_clean = sql.replace('\n', ' ').replace('\t', ' ')
    
    tables = set()
    
    # Apply each pattern
    for pattern in SQL_TABLE_PATTERNS:
        matches = pattern.findall(sql_clean)
        for match in matches:
            table_name = match.strip().lower()
            # Ignore SQL keywords
            if table_name and table_name not in IGNORE_KEYWORDS:
                tables.add(table_name)
    
    return sorted(list(tables))


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
    
    # Detect DB system and extract tables
    db_spans["db_system"] = db_spans["operation_name"].apply(_detect_db_system)
    db_spans["db_statement"] = db_spans["operation_name"]
    db_spans["tables"] = db_spans["operation_name"].apply(_extract_tables_from_sql)
    
    # Convert tables list to comma-separated string
    db_spans["tables"] = db_spans["tables"].apply(lambda x: ",".join(x) if x else "")
    
    # Select columns
    result = db_spans[[
        "trace_id", "span_id", "service_name", "db_system", "db_statement", "tables"
    ]].copy()
    
    return result


def save_db_operations_csv(df: pd.DataFrame, output_path: Path) -> None:
    """Save DB operations DataFrame to CSV."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(output_path, index=False)
