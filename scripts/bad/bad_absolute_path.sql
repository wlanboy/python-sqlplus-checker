WHENEVER SQLERROR EXIT FAILURE ROLLBACK
SET DEFINE OFF
SET SQLBLANKLINES ON
SET SERVEROUTPUT ON
SPOOL /tmp/bad_absolute_path.log

-- WARNING: absolute path in @ call (Unix)
@/opt/scripts/sub.sql

-- WARNING: absolute path in @@ call
@@/opt/scripts/sub2.sql

SPOOL OFF
EXIT;
