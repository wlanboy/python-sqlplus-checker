#!/usr/bin/env python3
"""sqlplus-checker — Static analyzer for Oracle SQLPlus compatibility.

Checks SQL scripts for issues that prevent successful execution in SQLPlus
even though they may work fine in SQL Developer or Toad.

Rules source: rules.md
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

SQL_EXTENSIONS: frozenset[str] = frozenset({
    ".sql", ".pls", ".pks", ".pkb",
    ".prc", ".fnc", ".trg", ".vw",
    ".tps", ".tpb",
})

# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


class Severity(Enum):
    ERROR = "ERROR"
    WARNING = "WARNING"


@dataclass(order=True)
class Issue:
    line_no: int          # 0 → file-level (no specific line)
    severity: Severity
    message: str

    def format(self) -> str:
        tag = "[ERROR]  " if self.severity == Severity.ERROR else "[WARNING]"
        if self.line_no:
            return f"  {tag} line {self.line_no:>4}: {self.message}"
        return f"  {tag}            {self.message}"


# ---------------------------------------------------------------------------
# Regex patterns
# ---------------------------------------------------------------------------

# PL/SQL block starters
RE_PLSQL_START = re.compile(
    r"^\s*(?:"
    r"CREATE\s+(?:OR\s+REPLACE\s+)?(?:EDITIONABLE\s+|NONEDITIONABLE\s+)?"
    r"(?:PROCEDURE|FUNCTION|PACKAGE(?:\s+BODY)?|TRIGGER|TYPE(?:\s+BODY)?)"
    r"|DECLARE"
    r"|BEGIN"
    r")\b",
    re.IGNORECASE,
)

# Slash on its own line — PL/SQL block terminator
RE_SLASH = re.compile(r"^\s*/\s*$")

# Top-level SQL statements that need a trailing semicolon
RE_SQL_STMT = re.compile(
    r"^\s*(?:"
    r"SELECT|INSERT|UPDATE|DELETE|MERGE"
    r"|CREATE\s+(?:(?:GLOBAL\s+)?TEMPORARY\s+)?TABLE"
    r"|CREATE\s+(?:UNIQUE\s+|BITMAP\s+)?INDEX"
    r"|CREATE\s+(?:MATERIALIZED\s+)?VIEW"
    r"|CREATE\s+SEQUENCE|CREATE\s+SYNONYM"
    r"|CREATE\s+(?:PUBLIC\s+)?DATABASE\s+LINK"
    r"|ALTER\s+(?:TABLE|INDEX|SEQUENCE|SESSION|SYSTEM)"
    r"|DROP\s+(?:TABLE|INDEX|SEQUENCE|VIEW|SYNONYM"
    r"|PROCEDURE|FUNCTION|PACKAGE|TRIGGER|TYPE)"
    r"|TRUNCATE|GRANT|REVOKE|COMMENT\s+ON|ANALYZE|COMMIT|ROLLBACK"
    r")\b",
    re.IGNORECASE,
)

# DML that should be followed by COMMIT or ROLLBACK
RE_DML = re.compile(r"^\s*(?:INSERT|UPDATE|DELETE|MERGE)\b", re.IGNORECASE)

# COMMIT / ROLLBACK as standalone statements (line-start match avoids matching
# ROLLBACK inside  WHENEVER SQLERROR EXIT FAILURE ROLLBACK)
RE_COMMIT_ROLLBACK = re.compile(r"^\s*(?:COMMIT|ROLLBACK)\b", re.IGNORECASE)

# SQLPlus commands that act as statement boundaries (not SQL, no semicolon expected)
RE_SQLPLUS_CMD = re.compile(
    r"^\s*(?:SET|SPOOL|EXIT|QUIT|WHENEVER|CONNECT|DISCONNECT|"
    r"PROMPT|PAUSE|ACCEPT|HOST|REMARK|REM|SHOW|VARIABLE|VAR|"
    r"EXECUTE|EXEC|DEFINE|COLUMN|COL|BREAK|COMPUTE|CLEAR|TTITLE|BTITLE)\b",
    re.IGNORECASE,
)

# ── Rule 1: Required header settings ──────────────────────────────────────

# WHENEVER SQLERROR EXIT FAILURE (or ROLLBACK or similar)
RE_WHENEVER = re.compile(
    r"^\s*WHENEVER\s+SQLERROR\s+EXIT\b", re.IGNORECASE,
)

# SET DEFINE OFF
RE_SET_DEFINE_OFF = re.compile(r"^\s*SET\s+DEFINE\s+OFF\b", re.IGNORECASE)

# SET SQLBLANKLINES ON  — when set, empty lines inside PL/SQL blocks are allowed
RE_SQLBLANKLINES_ON = re.compile(r"^\s*SET\s+SQLBLANKLINES\s+ON\b", re.IGNORECASE)

# SET SERVEROUTPUT ON
RE_SERVEROUTPUT_ON = re.compile(r"^\s*SET\s+SERVEROUTPUT\s+ON\b", re.IGNORECASE)

# ── Rule 3: Encoding / special characters ─────────────────────────────────

# Non-ASCII character in a comment (detect umlauts / special chars in comments)
RE_NON_ASCII = re.compile(r"[^\x00-\x7F]")

# ── Rule 4: Absolute paths in @ calls ─────────────────────────────────────

# @/absolute/path  or  @C:\path  (Windows absolute)
RE_ABSOLUTE_PATH = re.compile(
    r"^\s*@@?\s*(?:/|[A-Za-z]:[/\\])", re.IGNORECASE,
)

# ── Rule 5: Deployment hygiene ─────────────────────────────────────────────

RE_SPOOL = re.compile(r"^\s*SPOOL\b", re.IGNORECASE)
RE_EXIT = re.compile(r"^\s*EXIT\s*;?\s*$", re.IGNORECASE)

# ── Reserved keywords as unquoted aliases ─────────────────────────────────

_RESERVED: frozenset[str] = frozenset({
    "ACCESS", "ADD", "ALL", "ALTER", "AND", "ANY", "AS", "ASC", "AUDIT",
    "BETWEEN", "BY", "CHAR", "CHECK", "CLUSTER", "COLUMN", "COMMENT",
    "COMPRESS", "CONNECT", "CREATE", "CURRENT", "DATE", "DECIMAL", "DEFAULT",
    "DELETE", "DESC", "DISTINCT", "DROP", "ELSE", "EXCLUSIVE", "EXISTS",
    "FILE", "FLOAT", "FOR", "FROM", "GRANT", "GROUP", "HAVING", "IDENTIFIED",
    "IMMEDIATE", "IN", "INCREMENT", "INDEX", "INITIAL", "INSERT", "INTEGER",
    "INTERSECT", "INTO", "IS", "LEVEL", "LIKE", "LOCK", "LONG", "MAXEXTENTS",
    "MINUS", "MLSLABEL", "MODE", "MODIFY", "NOAUDIT", "NOCOMPRESS", "NOT",
    "NOWAIT", "NULL", "NUMBER", "OF", "OFFLINE", "ON", "ONLINE", "OPTION",
    "OR", "ORDER", "PCTFREE", "PRIOR", "PUBLIC", "RAW", "RENAME", "RESOURCE",
    "REVOKE", "ROW", "ROWID", "ROWNUM", "ROWS", "SELECT", "SESSION", "SET",
    "SHARE", "SIZE", "SMALLINT", "START", "SUCCESSFUL", "SYNONYM", "SYSDATE",
    "TABLE", "THEN", "TO", "TRIGGER", "UID", "UNION", "UNIQUE", "UPDATE",
    "USER", "VALIDATE", "VALUES", "VARCHAR", "VARCHAR2", "VIEW", "WHENEVER",
    "WHERE", "WITH",
})

RE_KEYWORD_ALIAS = re.compile(
    r"\bAS\s+(" + "|".join(sorted(_RESERVED)) + r")\s*(?:[,)\s]|$)",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _strip_line_comment(line: str) -> str:
    """Remove -- comment from end of line (simplified — ignores strings)."""
    idx = line.find("--")
    return line[:idx] if idx >= 0 else line


def _effective(line: str) -> str:
    """Meaningful part of a line: no inline comment, stripped."""
    return _strip_line_comment(line).strip()


def _is_comment_only(line: str) -> bool:
    """True if the line (after stripping whitespace) is a -- comment."""
    return line.strip().startswith("--")


# ---------------------------------------------------------------------------
# Core checker
# ---------------------------------------------------------------------------


def check_file(path: Path) -> list[Issue]:  # noqa: C901
    issues: list[Issue] = []

    # ── Raw bytes ──────────────────────────────────────────────────────────
    try:
        raw = path.read_bytes()
    except OSError as exc:
        return [Issue(0, Severity.ERROR, f"Cannot read file: {exc}")]

    if not raw:
        return []

    # Windows line endings (CRLF)
    if b"\r\n" in raw:
        issues.append(Issue(
            0, Severity.ERROR,
            "Windows line endings (CRLF) — convert to LF before deploying",
        ))

    # Rule 3: UTF-8 BOM → ERROR (rule explicitly says "ohne BOM")
    bom = raw.startswith(b"\xef\xbb\xbf")
    if bom:
        issues.append(Issue(
            0, Severity.ERROR,
            "UTF-8 BOM detected — rule requires UTF-8 *without* BOM; "
            "SQLPlus may reject the file",
        ))
        raw = raw[3:]

    # UTF-8 encoding
    try:
        content = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        issues.append(Issue(0, Severity.ERROR, f"File is not valid UTF-8: {exc}"))
        return issues

    lines = content.splitlines()
    if not lines:
        return issues

    # ── Pre-scan: collect file-level settings ──────────────────────────────
    has_whenever = False
    has_define_off = False
    has_sqlblanklines_on = False
    has_serveroutput = False
    has_spool = False
    has_exit = False
    has_dml = False
    has_commit_rollback = False

    for ln in lines:
        eff = _effective(ln)
        if RE_WHENEVER.match(eff):
            has_whenever = True
        if RE_SET_DEFINE_OFF.match(eff):
            has_define_off = True
        if RE_SQLBLANKLINES_ON.match(eff):
            has_sqlblanklines_on = True
        if RE_SERVEROUTPUT_ON.match(eff):
            has_serveroutput = True
        if RE_SPOOL.match(eff):
            has_spool = True
        if RE_EXIT.match(eff):
            has_exit = True
        if RE_DML.match(eff):
            has_dml = True
        if RE_COMMIT_ROLLBACK.search(eff):
            has_commit_rollback = True

    # ── Rule 1: Required header settings ──────────────────────────────────
    if not has_whenever:
        issues.append(Issue(
            0, Severity.WARNING,
            "Missing WHENEVER SQLERROR EXIT FAILURE — script continues on error "
            "(critical for deployment chains)",
        ))
    if not has_define_off:
        issues.append(Issue(
            0, Severity.WARNING,
            "Missing SET DEFINE OFF — '&' will be treated as substitution variable",
        ))
    if not has_sqlblanklines_on:
        issues.append(Issue(
            0, Severity.WARNING,
            "Missing SET SQLBLANKLINES ON — empty lines will terminate PL/SQL blocks "
            "(SQLPlus default: SQLBLANKLINES OFF)",
        ))
    if not has_serveroutput:
        issues.append(Issue(
            0, Severity.WARNING,
            "Missing SET SERVEROUTPUT ON — DBMS_OUTPUT will be invisible",
        ))

    # ── Rule 5: Deployment hygiene ─────────────────────────────────────────
    if not has_spool:
        issues.append(Issue(
            0, Severity.WARNING,
            "Missing SPOOL — no log file will be written for operations review",
        ))
    if not has_exit:
        issues.append(Issue(
            0, Severity.WARNING,
            "Missing EXIT; at end of script — SQLPlus will not return control "
            "to the shell after execution",
        ))

    # ── Rule 3: DML without COMMIT/ROLLBACK ───────────────────────────────
    if has_dml and not has_commit_rollback:
        issues.append(Issue(
            0, Severity.WARNING,
            "DML statements found but neither COMMIT nor ROLLBACK is present",
        ))

    # ── State machine ──────────────────────────────────────────────────────
    in_plsql = False
    plsql_start_line = 0
    in_block_comment = False

    # Semicolon tracking for top-level SQL statements
    stmt_start: int | None = None
    stmt_has_semi = False

    for line_no, line in enumerate(lines, start=1):
        raw_stripped = line.strip()
        eff = _effective(line)

        # ── Ampersand substitution variables ──────────────────────────────
        if not has_define_off and "&" in _strip_line_comment(line):
            issues.append(Issue(
                line_no, Severity.ERROR,
                "Substitution variable '&' — SET DEFINE OFF is missing",
            ))

        # ── Rule 4: Absolute paths in @ calls ─────────────────────────────
        if RE_ABSOLUTE_PATH.match(eff):
            issues.append(Issue(
                line_no, Severity.WARNING,
                "Absolute path in @ call — use relative paths (e.g. @./sub.sql)",
            ))

        # ── PL/SQL block mode ──────────────────────────────────────────────
        if in_plsql:
            # Empty lines terminate the buffer when SQLBLANKLINES is OFF.
            # Only flag if SET SQLBLANKLINES ON is absent (which we already warned about).
            if raw_stripped == "" and not has_sqlblanklines_on:
                issues.append(Issue(
                    line_no, Severity.ERROR,
                    "Empty line inside PL/SQL block with SQLBLANKLINES OFF — "
                    "SQLPlus terminates the block prematurely",
                ))

            # Block-comment tracking inside PL/SQL (for keyword / non-ASCII checks)
            if in_block_comment:
                if RE_NON_ASCII.search(line):
                    issues.append(Issue(
                        line_no, Severity.WARNING,
                        "Non-ASCII character in block comment — avoid umlauts/special "
                        "chars; NLS_LANG mismatch can corrupt output",
                    ))
                if "*/" in line:
                    in_block_comment = False
                continue

            if "/*" in eff and "*/" not in eff[eff.index("/*"):]:
                in_block_comment = True
                continue

            # Rule 3: non-ASCII in line comment inside PL/SQL
            if _is_comment_only(line) and RE_NON_ASCII.search(line):
                issues.append(Issue(
                    line_no, Severity.WARNING,
                    "Non-ASCII character in comment — avoid umlauts/special chars; "
                    "NLS_LANG mismatch between client and server",
                ))

            # Terminating slash → leave PL/SQL mode
            if RE_SLASH.match(line):
                in_plsql = False
                stmt_start = None
                stmt_has_semi = False

            continue

        # ── SQL mode ───────────────────────────────────────────────────────

        # Block comment tracking
        if in_block_comment:
            if RE_NON_ASCII.search(line):
                issues.append(Issue(
                    line_no, Severity.WARNING,
                    "Non-ASCII character in block comment — avoid umlauts/special chars",
                ))
            if "*/" in line:
                in_block_comment = False
            continue

        if "/*" in eff and "*/" not in eff[eff.index("/*"):]:
            in_block_comment = True
            continue

        # Rule 3: non-ASCII in single-line block comment  /* ... */
        if "/*" in line and "*/" in line:
            bc_s = line.find("/*")
            bc_e = line.find("*/", bc_s)
            if RE_NON_ASCII.search(line[bc_s: bc_e + 2]):
                issues.append(Issue(
                    line_no, Severity.WARNING,
                    "Non-ASCII character in block comment — avoid umlauts/special chars",
                ))

        # Rule 3: non-ASCII in line comment (SQL mode)
        if _is_comment_only(line) and RE_NON_ASCII.search(line):
            issues.append(Issue(
                line_no, Severity.WARNING,
                "Non-ASCII character in comment — avoid umlauts/special chars",
            ))

        if not eff or _is_comment_only(line):
            continue

        # PL/SQL block start?
        if RE_PLSQL_START.match(eff):
            if stmt_start is not None and not stmt_has_semi:
                issues.append(Issue(
                    stmt_start, Severity.ERROR,
                    "SQL statement has no terminating ';'",
                ))
            stmt_start = None
            in_plsql = True
            plsql_start_line = line_no
            continue

        # SQLPlus commands (SET, SPOOL, EXIT, …) are not SQL — they act as
        # statement boundaries and must never carry a semicolon to the previous stmt.
        if RE_SQLPLUS_CMD.match(eff):
            if stmt_start is not None and not stmt_has_semi:
                issues.append(Issue(
                    stmt_start, Severity.ERROR,
                    "SQL statement has no terminating ';'",
                ))
            stmt_start = None
            stmt_has_semi = False
            continue

        # Top-level SQL statement?
        if RE_SQL_STMT.match(eff):
            if stmt_start is not None and not stmt_has_semi:
                issues.append(Issue(
                    stmt_start, Severity.ERROR,
                    "SQL statement has no terminating ';'",
                ))
            stmt_start = line_no
            stmt_has_semi = ";" in eff
        elif stmt_start is not None:
            if ";" in eff:
                stmt_has_semi = True

        # Reserved keyword as unquoted alias
        m = RE_KEYWORD_ALIAS.search(eff)
        if m:
            kw = m.group(1).upper()
            issues.append(Issue(
                line_no, Severity.WARNING,
                f"Reserved keyword '{kw}' used as unquoted alias after AS",
            ))

    # ── Post-scan ──────────────────────────────────────────────────────────

    if in_plsql:
        issues.append(Issue(
            plsql_start_line, Severity.ERROR,
            "PL/SQL block not terminated with '/' on its own line",
        ))

    if stmt_start is not None and not stmt_has_semi:
        issues.append(Issue(
            stmt_start, Severity.ERROR,
            "SQL statement has no terminating ';'",
        ))

    return sorted(issues)


# ---------------------------------------------------------------------------
# Directory scanner
# ---------------------------------------------------------------------------


def collect_files(root: Path, extensions: frozenset[str]) -> list[Path]:
    if root.is_file():
        return [root]
    return sorted(
        p for p in root.rglob("*")
        if p.is_file() and p.suffix.lower() in extensions
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

SEPARATOR = "─" * 68


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="sqlplus-checker",
        description=(
            "Static analysis of Oracle SQL scripts for SQLPlus compatibility.\n"
            "Exit code: 0 = no errors, 1 = errors found, 2 = usage error."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "path",
        type=Path,
        help="File or directory to check (directories are scanned recursively)",
    )
    parser.add_argument(
        "--ext", "-e",
        metavar="EXT",
        default=",".join(sorted(SQL_EXTENSIONS)),
        help=(
            "Comma-separated extensions to include "
            f"(default: {','.join(sorted(SQL_EXTENSIONS))})"
        ),
    )
    parser.add_argument(
        "--no-warnings", "-W",
        action="store_true",
        help="Suppress warnings, only report errors",
    )
    parser.add_argument(
        "--summary-only", "-s",
        action="store_true",
        help="Print only the summary, not individual issues",
    )

    args = parser.parse_args()

    extensions = frozenset(
        e.strip() if e.strip().startswith(".") else f".{e.strip()}"
        for e in args.ext.split(",")
        if e.strip()
    )

    root: Path = args.path
    if not root.exists():
        parser.error(f"Path not found: {root}")

    files = collect_files(root, extensions)
    if not files:
        print(f"No SQL files found in: {root}")
        sys.exit(0)

    total_errors = 0
    total_warnings = 0
    files_with_issues = 0

    for filepath in files:
        issues = check_file(filepath)

        if args.no_warnings:
            issues = [i for i in issues if i.severity == Severity.ERROR]

        errors = sum(1 for i in issues if i.severity == Severity.ERROR)
        warnings = sum(1 for i in issues if i.severity == Severity.WARNING)
        total_errors += errors
        total_warnings += warnings

        if issues:
            files_with_issues += 1
            if not args.summary_only:
                try:
                    rel = filepath.relative_to(root) if root.is_dir() else filepath
                except ValueError:
                    rel = filepath
                print(f"\n{rel}")
                for issue in issues:
                    print(issue.format())

    print(f"\n{SEPARATOR}")
    print(f"Files checked    : {len(files)}")
    print(f"Files with issues: {files_with_issues}")
    print(f"Errors           : {total_errors}")
    if not args.no_warnings:
        print(f"Warnings         : {total_warnings}")
    print(SEPARATOR)

    sys.exit(1 if total_errors > 0 else 0)


if __name__ == "__main__":
    main()
