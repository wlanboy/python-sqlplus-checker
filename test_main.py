"""Pytest suite for sqlplus-checker — covers all rules from rules.md."""

from __future__ import annotations

import pytest
from pathlib import Path

from main import check_file, Issue, Severity


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def write_sql(tmp_path: Path, content: bytes, name: str = "test.sql") -> Path:
    p = tmp_path / name
    p.write_bytes(content)
    return p


def errors(issues: list[Issue]) -> list[Issue]:
    return [i for i in issues if i.severity == Severity.ERROR]


def warnings(issues: list[Issue]) -> list[Issue]:
    return [i for i in issues if i.severity == Severity.WARNING]


def has_message(issues: list[Issue], fragment: str) -> bool:
    return any(fragment.lower() in i.message.lower() for i in issues)


# Minimal compliant script — passes all header / hygiene rules.
FULL_HEADER = (
    b"WHENEVER SQLERROR EXIT FAILURE ROLLBACK\n"
    b"SET DEFINE OFF\n"
    b"SET SQLBLANKLINES ON\n"
    b"SET SERVEROUTPUT ON\n"
    b"SPOOL /tmp/test.log\n"
)
FULL_FOOTER = b"SPOOL OFF\nEXIT;\n"


def compliant(body: bytes) -> bytes:
    """Wrap body in compliant header + footer."""
    return FULL_HEADER + body + FULL_FOOTER


# ---------------------------------------------------------------------------
# Rule 3 — Encoding & line endings (checked first on raw bytes)
# ---------------------------------------------------------------------------

class TestEncoding:

    def test_valid_utf8_no_bom_no_issues(self, tmp_path):
        p = write_sql(tmp_path, compliant(b"-- ok\n"))
        result = check_file(p)
        assert not errors(result)

    def test_utf8_bom_is_error(self, tmp_path):
        bom = b"\xef\xbb\xbf"
        p = write_sql(tmp_path, bom + compliant(b"-- ok\n"))
        result = check_file(p)
        assert has_message(errors(result), "bom")

    def test_non_utf8_is_error(self, tmp_path):
        # Latin-1 encoded umlaut — not valid UTF-8
        p = write_sql(tmp_path, b"-- Gr\xfc\xdf Gott\n")
        result = check_file(p)
        assert has_message(errors(result), "utf-8")

    def test_crlf_is_error(self, tmp_path):
        p = write_sql(tmp_path, compliant(b"-- windows\r\n"))
        result = check_file(p)
        assert has_message(errors(result), "crlf")

    def test_lf_only_no_crlf_error(self, tmp_path):
        p = write_sql(tmp_path, compliant(b"-- unix\n"))
        result = check_file(p)
        assert not has_message(errors(result), "crlf")

    def test_empty_file_no_issues(self, tmp_path):
        p = write_sql(tmp_path, b"")
        result = check_file(p)
        assert result == []


# ---------------------------------------------------------------------------
# Rule 3 — Non-ASCII in comments
# ---------------------------------------------------------------------------

class TestNonAsciiInComments:

    def test_umlaut_in_line_comment_is_warning(self, tmp_path):
        # UTF-8 encoded ü = \xc3\xbc
        p = write_sql(tmp_path, compliant("-- Grüße\n".encode("utf-8")))
        result = check_file(p)
        assert has_message(warnings(result), "non-ascii")

    def test_umlaut_in_block_comment_is_warning(self, tmp_path):
        p = write_sql(tmp_path, compliant("/* Grüße */\n".encode("utf-8")))
        result = check_file(p)
        assert has_message(warnings(result), "non-ascii")

    def test_ascii_comment_no_warning(self, tmp_path):
        p = write_sql(tmp_path, compliant(b"-- plain ascii comment\n"))
        result = check_file(p)
        assert not has_message(warnings(result), "non-ascii")


# ---------------------------------------------------------------------------
# Rule 1 — Required header settings
# ---------------------------------------------------------------------------

class TestHeaderSettings:

    def test_missing_whenever_is_warning(self, tmp_path):
        script = (
            b"SET DEFINE OFF\n"
            b"SET SQLBLANKLINES ON\n"
            b"SET SERVEROUTPUT ON\n"
            b"SPOOL x.log\n"
            b"EXIT;\n"
        )
        p = write_sql(tmp_path, script)
        result = check_file(p)
        assert has_message(warnings(result), "whenever")

    def test_missing_define_off_is_warning(self, tmp_path):
        script = (
            b"WHENEVER SQLERROR EXIT FAILURE ROLLBACK\n"
            b"SET SQLBLANKLINES ON\n"
            b"SET SERVEROUTPUT ON\n"
            b"SPOOL x.log\n"
            b"EXIT;\n"
        )
        p = write_sql(tmp_path, script)
        result = check_file(p)
        assert has_message(warnings(result), "define off")

    def test_missing_sqlblanklines_on_is_warning(self, tmp_path):
        script = (
            b"WHENEVER SQLERROR EXIT FAILURE ROLLBACK\n"
            b"SET DEFINE OFF\n"
            b"SET SERVEROUTPUT ON\n"
            b"SPOOL x.log\n"
            b"EXIT;\n"
        )
        p = write_sql(tmp_path, script)
        result = check_file(p)
        assert has_message(warnings(result), "sqlblanklines")

    def test_missing_serveroutput_is_warning(self, tmp_path):
        script = (
            b"WHENEVER SQLERROR EXIT FAILURE ROLLBACK\n"
            b"SET DEFINE OFF\n"
            b"SET SQLBLANKLINES ON\n"
            b"SPOOL x.log\n"
            b"EXIT;\n"
        )
        p = write_sql(tmp_path, script)
        result = check_file(p)
        assert has_message(warnings(result), "serveroutput")

    def test_all_header_settings_present_no_header_warnings(self, tmp_path):
        p = write_sql(tmp_path, compliant(b"-- body\n"))
        result = check_file(p)
        header_warning_fragments = ["whenever", "define off", "sqlblanklines", "serveroutput"]
        for frag in header_warning_fragments:
            assert not has_message(result, frag), f"Unexpected warning for: {frag}"


# ---------------------------------------------------------------------------
# Rule 5 — Deployment hygiene
# ---------------------------------------------------------------------------

class TestDeploymentHygiene:

    def test_missing_spool_is_warning(self, tmp_path):
        script = (
            b"WHENEVER SQLERROR EXIT FAILURE ROLLBACK\n"
            b"SET DEFINE OFF\n"
            b"SET SQLBLANKLINES ON\n"
            b"SET SERVEROUTPUT ON\n"
            b"EXIT;\n"
        )
        p = write_sql(tmp_path, script)
        assert has_message(warnings(check_file(p)), "spool")

    def test_missing_exit_is_warning(self, tmp_path):
        script = (
            b"WHENEVER SQLERROR EXIT FAILURE ROLLBACK\n"
            b"SET DEFINE OFF\n"
            b"SET SQLBLANKLINES ON\n"
            b"SET SERVEROUTPUT ON\n"
            b"SPOOL x.log\n"
        )
        p = write_sql(tmp_path, script)
        assert has_message(warnings(check_file(p)), "exit")

    def test_spool_and_exit_present_no_hygiene_warnings(self, tmp_path):
        p = write_sql(tmp_path, compliant(b"-- body\n"))
        result = check_file(p)
        assert not has_message(result, "spool")
        assert not has_message(result, "exit")

    def test_exit_without_semicolon_accepted(self, tmp_path):
        script = FULL_HEADER + b"SPOOL OFF\nEXIT\n"
        p = write_sql(tmp_path, script)
        assert not has_message(check_file(p), "exit")


# ---------------------------------------------------------------------------
# Rule 2 — Slash logic: PL/SQL blocks
# ---------------------------------------------------------------------------

class TestPlsqlSlash:

    def test_procedure_with_slash_ok(self, tmp_path):
        body = (
            b"CREATE OR REPLACE PROCEDURE foo AS\n"
            b"BEGIN\n"
            b"  NULL;\n"
            b"END foo;\n"
            b"/\n"
        )
        p = write_sql(tmp_path, compliant(body))
        assert not has_message(errors(check_file(p)), "terminated")

    def test_procedure_missing_slash_is_error(self, tmp_path):
        body = (
            b"CREATE OR REPLACE PROCEDURE foo AS\n"
            b"BEGIN\n"
            b"  NULL;\n"
            b"END foo;\n"
            # no slash!
        )
        p = write_sql(tmp_path, compliant(body))
        assert has_message(errors(check_file(p)), "terminated")

    def test_function_missing_slash_is_error(self, tmp_path):
        body = (
            b"CREATE OR REPLACE FUNCTION bar RETURN NUMBER IS\n"
            b"BEGIN\n"
            b"  RETURN 1;\n"
            b"END bar;\n"
        )
        p = write_sql(tmp_path, compliant(body))
        assert has_message(errors(check_file(p)), "terminated")

    def test_package_spec_with_slash_ok(self, tmp_path):
        body = (
            b"CREATE OR REPLACE PACKAGE pkg AS\n"
            b"  PROCEDURE foo;\n"
            b"END pkg;\n"
            b"/\n"
        )
        p = write_sql(tmp_path, compliant(body))
        assert not has_message(errors(check_file(p)), "terminated")

    def test_package_body_with_slash_ok(self, tmp_path):
        body = (
            b"CREATE OR REPLACE PACKAGE BODY pkg AS\n"
            b"  PROCEDURE foo AS\n"
            b"  BEGIN\n"
            b"    NULL;\n"
            b"  END foo;\n"
            b"END pkg;\n"
            b"/\n"
        )
        p = write_sql(tmp_path, compliant(body))
        assert not has_message(errors(check_file(p)), "terminated")

    def test_trigger_missing_slash_is_error(self, tmp_path):
        body = (
            b"CREATE OR REPLACE TRIGGER trg\n"
            b"BEFORE INSERT ON t\n"
            b"BEGIN\n"
            b"  NULL;\n"
            b"END;\n"
        )
        p = write_sql(tmp_path, compliant(body))
        assert has_message(errors(check_file(p)), "terminated")

    def test_anonymous_begin_end_missing_slash_is_error(self, tmp_path):
        body = b"BEGIN\n  NULL;\nEND;\n"
        p = write_sql(tmp_path, compliant(body))
        assert has_message(errors(check_file(p)), "terminated")

    def test_declare_block_missing_slash_is_error(self, tmp_path):
        body = b"DECLARE\n  v NUMBER;\nBEGIN\n  NULL;\nEND;\n"
        p = write_sql(tmp_path, compliant(body))
        assert has_message(errors(check_file(p)), "terminated")

    def test_multiple_plsql_blocks_all_need_slash(self, tmp_path):
        body = (
            b"CREATE OR REPLACE PROCEDURE a AS\nBEGIN NULL; END;\n/\n"
            b"CREATE OR REPLACE PROCEDURE b AS\nBEGIN NULL; END;\n"
            # second block missing slash
        )
        p = write_sql(tmp_path, compliant(body))
        assert has_message(errors(check_file(p)), "terminated")

    def test_multiple_plsql_blocks_all_with_slash_ok(self, tmp_path):
        body = (
            b"CREATE OR REPLACE PROCEDURE a AS\nBEGIN NULL; END;\n/\n"
            b"CREATE OR REPLACE PROCEDURE b AS\nBEGIN NULL; END;\n/\n"
        )
        p = write_sql(tmp_path, compliant(body))
        assert not has_message(errors(check_file(p)), "terminated")

    def test_type_body_with_slash_ok(self, tmp_path):
        body = (
            b"CREATE OR REPLACE TYPE BODY my_type AS\n"
            b"  MEMBER FUNCTION val RETURN NUMBER IS\n"
            b"  BEGIN RETURN 1; END;\n"
            b"END;\n"
            b"/\n"
        )
        p = write_sql(tmp_path, compliant(body))
        assert not has_message(errors(check_file(p)), "terminated")


# ---------------------------------------------------------------------------
# Rule 2 — Slash logic: empty lines in PL/SQL blocks
# ---------------------------------------------------------------------------

class TestEmptyLinesInPlsql:

    def test_empty_line_in_plsql_without_sqlblanklines_is_error(self, tmp_path):
        # No SET SQLBLANKLINES ON in header
        script = (
            b"WHENEVER SQLERROR EXIT FAILURE ROLLBACK\n"
            b"SET DEFINE OFF\n"
            b"SET SERVEROUTPUT ON\n"
            b"SPOOL x.log\n"
            b"CREATE OR REPLACE PROCEDURE foo AS\n"
            b"BEGIN\n"
            b"\n"          # empty line — terminates buffer without SQLBLANKLINES ON
            b"  NULL;\n"
            b"END foo;\n"
            b"/\n"
            b"EXIT;\n"
        )
        p = write_sql(tmp_path, script)
        assert has_message(errors(check_file(p)), "empty line")

    def test_empty_line_in_plsql_with_sqlblanklines_on_is_ok(self, tmp_path):
        body = (
            b"CREATE OR REPLACE PROCEDURE foo AS\n"
            b"BEGIN\n"
            b"\n"          # empty line — OK because SET SQLBLANKLINES ON is set
            b"  NULL;\n"
            b"END foo;\n"
            b"/\n"
        )
        p = write_sql(tmp_path, compliant(body))  # compliant() includes SQLBLANKLINES ON
        assert not has_message(errors(check_file(p)), "empty line")

    def test_no_empty_line_in_plsql_no_error(self, tmp_path):
        body = (
            b"CREATE OR REPLACE PROCEDURE foo AS\n"
            b"BEGIN\n"
            b"  NULL;\n"
            b"END foo;\n"
            b"/\n"
        )
        p = write_sql(tmp_path, compliant(body))
        assert not has_message(errors(check_file(p)), "empty line")


# ---------------------------------------------------------------------------
# Rule 2 — Semicolons on SQL statements
# ---------------------------------------------------------------------------

class TestSemicolons:

    def test_select_with_semicolon_ok(self, tmp_path):
        p = write_sql(tmp_path, compliant(b"SELECT 1 FROM DUAL;\n"))
        assert not has_message(errors(check_file(p)), "terminating ';'")

    def test_select_missing_semicolon_is_error(self, tmp_path):
        p = write_sql(tmp_path, compliant(b"SELECT 1 FROM DUAL\n"))
        assert has_message(errors(check_file(p)), "terminating ';'")

    def test_insert_with_semicolon_ok(self, tmp_path):
        body = b"INSERT INTO t VALUES (1);\nCOMMIT;\n"
        p = write_sql(tmp_path, compliant(body))
        assert not has_message(errors(check_file(p)), "terminating ';'")

    def test_insert_missing_semicolon_is_error(self, tmp_path):
        body = b"INSERT INTO t VALUES (1)\nCOMMIT;\n"
        p = write_sql(tmp_path, compliant(body))
        assert has_message(errors(check_file(p)), "terminating ';'")

    def test_multiline_select_semicolon_on_last_line_ok(self, tmp_path):
        body = b"SELECT *\nFROM dual\nWHERE 1=1;\n"
        p = write_sql(tmp_path, compliant(body))
        assert not has_message(errors(check_file(p)), "terminating ';'")

    def test_multiline_select_missing_semicolon_is_error(self, tmp_path):
        body = b"SELECT *\nFROM dual\nWHERE 1=1\n"
        p = write_sql(tmp_path, compliant(body))
        assert has_message(errors(check_file(p)), "terminating ';'")

    def test_semicolons_inside_plsql_not_flagged_as_top_level(self, tmp_path):
        # Semicolons inside PL/SQL are normal statement terminators
        body = (
            b"CREATE OR REPLACE PROCEDURE foo AS\n"
            b"BEGIN\n"
            b"  INSERT INTO t VALUES (1);\n"
            b"  COMMIT;\n"
            b"END foo;\n"
            b"/\n"
        )
        p = write_sql(tmp_path, compliant(body))
        assert not has_message(errors(check_file(p)), "terminating ';'")

    def test_alter_table_missing_semicolon_is_error(self, tmp_path):
        p = write_sql(tmp_path, compliant(b"ALTER TABLE t ADD (col NUMBER)\n"))
        assert has_message(errors(check_file(p)), "terminating ';'")

    def test_create_table_missing_semicolon_is_error(self, tmp_path):
        body = b"CREATE TABLE t (id NUMBER)\n"
        p = write_sql(tmp_path, compliant(body))
        assert has_message(errors(check_file(p)), "terminating ';'")

    def test_grant_missing_semicolon_is_error(self, tmp_path):
        p = write_sql(tmp_path, compliant(b"GRANT SELECT ON t TO user1\n"))
        assert has_message(errors(check_file(p)), "terminating ';'")


# ---------------------------------------------------------------------------
# Ampersand substitution variables (Rule 1 / SET DEFINE OFF)
# ---------------------------------------------------------------------------

class TestAmpersand:

    def test_ampersand_without_define_off_is_error(self, tmp_path):
        # No SET DEFINE OFF in header
        script = (
            b"WHENEVER SQLERROR EXIT FAILURE ROLLBACK\n"
            b"SET SQLBLANKLINES ON\n"
            b"SET SERVEROUTPUT ON\n"
            b"SPOOL x.log\n"
            b"INSERT INTO t VALUES (&val);\n"
            b"COMMIT;\n"
            b"EXIT;\n"
        )
        p = write_sql(tmp_path, script)
        assert has_message(errors(check_file(p)), "&")

    def test_ampersand_with_define_off_no_error(self, tmp_path):
        body = b"INSERT INTO t VALUES (&val);\nCOMMIT;\n"
        p = write_sql(tmp_path, compliant(body))  # compliant includes SET DEFINE OFF
        assert not has_message(errors(check_file(p)), "&")

    def test_ampersand_in_line_comment_not_flagged(self, tmp_path):
        # & in a -- comment should be ignored
        script = (
            b"WHENEVER SQLERROR EXIT FAILURE ROLLBACK\n"
            b"SET SQLBLANKLINES ON\n"
            b"SET SERVEROUTPUT ON\n"
            b"SPOOL x.log\n"
            b"-- use &variable here\n"
            b"EXIT;\n"
        )
        p = write_sql(tmp_path, script)
        # The & is in a comment → stripped → no error for &
        assert not has_message(errors(check_file(p)), "substitution variable '&'")


# ---------------------------------------------------------------------------
# Rule 3 — DML without COMMIT / ROLLBACK
# ---------------------------------------------------------------------------

class TestDmlCommitRollback:

    def test_insert_without_commit_is_warning(self, tmp_path):
        body = b"INSERT INTO t VALUES (1);\n"
        p = write_sql(tmp_path, compliant(body))
        assert has_message(warnings(check_file(p)), "commit")

    def test_update_with_commit_no_warning(self, tmp_path):
        body = b"UPDATE t SET x=1;\nCOMMIT;\n"
        p = write_sql(tmp_path, compliant(body))
        assert not has_message(warnings(check_file(p)), "commit")

    def test_delete_with_rollback_no_warning(self, tmp_path):
        body = b"DELETE FROM t;\nROLLBACK;\n"
        p = write_sql(tmp_path, compliant(body))
        assert not has_message(warnings(check_file(p)), "commit")

    def test_select_only_no_commit_warning(self, tmp_path):
        body = b"SELECT 1 FROM DUAL;\n"
        p = write_sql(tmp_path, compliant(body))
        assert not has_message(warnings(check_file(p)), "commit")

    def test_merge_without_commit_is_warning(self, tmp_path):
        body = (
            b"MERGE INTO t USING src ON (t.id = src.id)\n"
            b"WHEN MATCHED THEN UPDATE SET t.val = src.val;\n"
        )
        p = write_sql(tmp_path, compliant(body))
        assert has_message(warnings(check_file(p)), "commit")


# ---------------------------------------------------------------------------
# Rule 4 — Absolute paths in @ calls
# ---------------------------------------------------------------------------

class TestAbsolutePaths:

    def test_unix_absolute_path_is_warning(self, tmp_path):
        body = b"@/opt/scripts/sub.sql\n"
        p = write_sql(tmp_path, compliant(body))
        assert has_message(warnings(check_file(p)), "absolute path")

    def test_windows_absolute_path_is_warning(self, tmp_path):
        body = b"@C:\\scripts\\sub.sql\n"
        p = write_sql(tmp_path, compliant(body))
        assert has_message(warnings(check_file(p)), "absolute path")

    def test_double_at_absolute_path_is_warning(self, tmp_path):
        body = b"@@/opt/scripts/sub.sql\n"
        p = write_sql(tmp_path, compliant(body))
        assert has_message(warnings(check_file(p)), "absolute path")

    def test_relative_at_call_no_warning(self, tmp_path):
        body = b"@./sub_script.sql\n"
        p = write_sql(tmp_path, compliant(body))
        assert not has_message(warnings(check_file(p)), "absolute path")

    def test_double_at_relative_call_no_warning(self, tmp_path):
        body = b"@@sub_script.sql\n"
        p = write_sql(tmp_path, compliant(body))
        assert not has_message(warnings(check_file(p)), "absolute path")


# ---------------------------------------------------------------------------
# Reserved keyword as alias (Warning)
# ---------------------------------------------------------------------------

class TestReservedKeywordAlias:

    def test_reserved_keyword_after_as_is_warning(self, tmp_path):
        body = b"SELECT sysdate AS DATE FROM DUAL;\n"
        p = write_sql(tmp_path, compliant(body))
        assert has_message(warnings(check_file(p)), "reserved keyword")

    def test_quoted_keyword_alias_no_warning(self, tmp_path):
        body = b'SELECT sysdate AS "DATE" FROM DUAL;\n'
        p = write_sql(tmp_path, compliant(body))
        assert not has_message(warnings(check_file(p)), "reserved keyword")

    def test_non_reserved_alias_no_warning(self, tmp_path):
        body = b"SELECT sysdate AS event_date FROM DUAL;\n"
        p = write_sql(tmp_path, compliant(body))
        assert not has_message(warnings(check_file(p)), "reserved keyword")


# ---------------------------------------------------------------------------
# Fully compliant script → zero issues
# ---------------------------------------------------------------------------

class TestCompliantScript:

    def test_fully_compliant_procedure_script_no_issues(self, tmp_path):
        body = (
            b"CREATE OR REPLACE PROCEDURE do_work AS\n"
            b"BEGIN\n"
            b"  INSERT INTO log_table (msg) VALUES ('done');\n"
            b"  COMMIT;\n"
            b"END do_work;\n"
            b"/\n"
        )
        p = write_sql(tmp_path, compliant(body))
        result = check_file(p)
        assert errors(result) == [], f"Unexpected errors: {[i.message for i in errors(result)]}"

    def test_fully_compliant_dml_script_no_issues(self, tmp_path):
        body = b"INSERT INTO t (id) VALUES (42);\nCOMMIT;\n"
        p = write_sql(tmp_path, compliant(body))
        result = check_file(p)
        assert errors(result) == [], f"Unexpected errors: {[i.message for i in errors(result)]}"

    def test_fully_compliant_package_no_issues(self, tmp_path):
        body = (
            b"CREATE OR REPLACE PACKAGE BODY pkg AS\n"
            b"  PROCEDURE foo AS\n"
            b"  BEGIN\n"
            b"    NULL;\n"
            b"  END foo;\n"
            b"END pkg;\n"
            b"/\n"
        )
        p = write_sql(tmp_path, compliant(body))
        result = check_file(p)
        assert errors(result) == [], f"Unexpected errors: {[i.message for i in errors(result)]}"


# ---------------------------------------------------------------------------
# Directory scanning
# ---------------------------------------------------------------------------

class TestDirectoryScanning:

    def test_scans_subdirectories_recursively(self, tmp_path):
        from main import collect_files, SQL_EXTENSIONS

        sub = tmp_path / "sub" / "deep"
        sub.mkdir(parents=True)
        (tmp_path / "a.sql").write_bytes(b"")
        (sub / "b.pks").write_bytes(b"")
        (sub / "ignored.txt").write_bytes(b"")

        files = collect_files(tmp_path, SQL_EXTENSIONS)
        names = {f.name for f in files}
        assert "a.sql" in names
        assert "b.pks" in names
        assert "ignored.txt" not in names

    def test_all_sql_extensions_detected(self, tmp_path):
        from main import collect_files, SQL_EXTENSIONS

        for ext in SQL_EXTENSIONS:
            (tmp_path / f"file{ext}").write_bytes(b"")

        files = collect_files(tmp_path, SQL_EXTENSIONS)
        assert len(files) == len(SQL_EXTENSIONS)
