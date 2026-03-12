# sqlplus-checker

Statische Analyse von Oracle SQL-Skripten auf SQLPlus-Kompatibilität.

Viele Skripte funktionieren problemlos in SQL Developer oder Toad, scheitern aber beim Deployment über SQLPlus auf dem Linux-Server. Dieses Tool prüft Skripte **rein statisch** (ohne Datenbankverbindung) auf die häufigsten Fehlerquellen.

---

## Installation

```bash
cd python-sqlplus-checker
uv sync
uv lock --upgrade
```

Mit `uv run python main.py` ausführbar.

---

## Verwendung

```text
sqlplus-checker <PFAD> [OPTIONEN]
```

### Argumente

| Argument | Beschreibung |
| --- | --- |
| `PFAD` | Datei oder Verzeichnis — Verzeichnisse werden rekursiv durchsucht |

### Optionen

| Option | Kurzform | Beschreibung |
| --- | --- | --- |
| `--ext EXT` | `-e EXT` | Kommagetrennte Dateiendungen (Standard: `.sql,.pls,.pks,.pkb,.prc,.fnc,.trg,.vw,.tps,.tpb`) |
| `--no-warnings` | `-W` | Nur Fehler ausgeben, Warnungen unterdrücken |
| `--summary-only` | `-s` | Nur die Zusammenfassung ausgeben, keine Einzelmeldungen |

### Exit-Codes

| Code | Bedeutung |
| --- | --- |
| `0` | Keine Fehler gefunden (Warnungen möglich) |
| `1` | Mindestens ein Fehler gefunden |
| `2` | Ungültige Aufrufargumente |

---

## Beispiele

**Einzelne Datei prüfen:**

```bash
sqlplus-checker deploy.sql
```

**Komplettes Verzeichnis rekursiv prüfen:**

```bash
sqlplus-checker ./scripts/
```

**Nur Fehler anzeigen (keine Warnungen), z. B. für CI/CD-Pipeline:**

```bash
sqlplus-checker ./scripts/ --no-warnings
```

**Nur die Zusammenfassung anzeigen:**

```bash
sqlplus-checker ./scripts/ --summary-only
```

**Andere Dateiendungen einschließen:**

```bash
sqlplus-checker ./scripts/ --ext .sql,.ddl,.dml
```

**In CI/CD-Pipelines (Exit-Code auswerten):**

```bash
sqlplus-checker ./scripts/ --no-warnings
if [ $? -ne 0 ]; then
  echo "SQL-Prüfung fehlgeschlagen — Deployment abgebrochen"
  exit 1
fi
```

---

## Geprüfte Regeln

### Fehler (ERROR) — blockieren das Deployment

| Regel | Beschreibung |
| --- | --- |
| UTF-8-Kodierung | Datei muss gültiges UTF-8 ohne BOM sein |
| Windows-Zeilenenden (CRLF) | SQLPlus unter Linux erwartet LF |
| Fehlendes `/` nach PL/SQL-Block | `PROCEDURE`, `FUNCTION`, `PACKAGE`, `TRIGGER`, `TYPE` und `BEGIN`/`END`-Blöcke brauchen `/` in einer eigenen Zeile |
| Leerzeile in PL/SQL-Block | Ohne `SET SQLBLANKLINES ON` bricht SQLPlus den Block bei einer Leerzeile ab |
| Fehlendes `;` bei SQL-Statements | `SELECT`, `INSERT`, `UPDATE`, `DELETE`, `ALTER`, `CREATE TABLE` usw. müssen mit `;` enden |
| `&` ohne `SET DEFINE OFF` | SQLPlus fragt sonst interaktiv nach dem Substitutionswert |

### Warnungen (WARNING) — sollten vor dem Deployment behoben werden

| Regel | Beschreibung |
| --- | --- |
| Fehlendes `WHENEVER SQLERROR EXIT FAILURE` | Ohne diese Einstellung läuft das Skript bei einem Fehler einfach weiter |
| Fehlendes `SET DEFINE OFF` | `&` im Code wird als Substitutionsvariable interpretiert |
| Fehlendes `SET SQLBLANKLINES ON` | Leerzeilen in PL/SQL-Blöcken brechen den Block ab |
| Fehlendes `SET SERVEROUTPUT ON` | `DBMS_OUTPUT`-Ausgaben sind unsichtbar |
| Fehlendes `SPOOL` | Kein Log-File für den Betrieb |
| Fehlendes `EXIT;` | SQLPlus gibt die Shell-Kontrolle nicht zurück |
| DML ohne `COMMIT`/`ROLLBACK` | `INSERT`/`UPDATE`/`DELETE`/`MERGE` ohne abschließende Transaktion |
| Absoluter Pfad in `@`-Aufruf | Absolute Pfade sind umgebungsabhängig — relative Pfade verwenden |
| Nicht-ASCII in Kommentaren | Umlaute können bei NLS_LANG-Mismatch zwischen Client und Server Probleme verursachen |
| Reserviertes Keyword als Alias | Oracle-Keywords (z. B. `DATE`, `TABLE`) unquotiert nach `AS` |

---

## Empfohlener Skript-Header

Jedes Deployment-Skript sollte mit folgendem Header beginnen:

```sql
WHENEVER SQLERROR EXIT FAILURE ROLLBACK
SET DEFINE OFF
SET SQLBLANKLINES ON
SET SERVEROUTPUT ON
SPOOL /pfad/zum/logfile.log

-- ... Skript-Inhalt ...

SPOOL OFF
EXIT;
```

---

## Ergänzende Syntaxprüfung mit SQLcl

`sqlplus-checker` prüft statisch ohne Datenbankverbindung — SQLcl kennt hingegen
den vollständigen Oracle-SQL/PL/SQL-Parser und findet echte Syntaxfehler, die
kein statisches Tool erkennen kann.

**Empfohlenes Vorgehen (zweistufig):**

| Stufe | Tool | Was wird geprüft |
| --- | --- | --- |
| 1 | `sqlplus-checker` | SQLPlus-Kompatibilität, Encoding, Header, Slash-Logik |
| 2 | SQLcl (Docker) | Echter Oracle-Syntaxparser, PL/SQL-Semantik |

### SQLcl-Prüfung per Docker

SQLcl steht als offizielles Oracle Docker-Image zur Verfügung — keine lokale
Installation nötig. Das folgende Kommando prüft **alle SQL-Dateien rekursiv**
im angegebenen Verzeichnis:

```bash
docker run --rm \
  -v /pfad/zu/scripts:/scripts \
  container-registry.oracle.com/database/sqlcl:latest \
  /bin/bash -c '
    errors=0; checked=0
    while IFS= read -r -d "" f; do
      rel="${f#/scripts/}"
      printf "Prüfe: %s ... " "$rel"
      checked=$((checked + 1))
      output=$(printf "WHENEVER SQLERROR EXIT FAILURE\nSET FEEDBACK OFF\nSET TERMOUT OFF\nSET HEADING OFF\n@%s\nEXIT\n" "$f" \
        | /opt/oracle/sqlcl/bin/sql -S -noupdates /nolog 2>&1)
      rc=$?
      if [ $rc -ne 0 ]; then
        echo "FEHLER"
        echo "$output" | sed "s/^/  /"
        errors=$((errors + 1))
      else
        echo "OK"
      fi
    done < <(find /scripts \
      \( -name "*.sql" -o -name "*.pls" -o -name "*.pks" -o -name "*.pkb" \
         -o -name "*.prc" -o -name "*.fnc" -o -name "*.trg" \) \
      -print0 | sort -z)
    echo ""
    echo "Ergebnis: $checked Dateien geprüft, $errors mit Syntaxfehler"
    exit $([ "$errors" -eq 0 ] && echo 0 || echo 1)
  '
```

### Erklärung der SQLcl-Optionen

| Option / Befehl | Bedeutung |
| --- | --- |
| `-S` | Silent-Modus — keine Verbindungsbanner |
| `-noupdates` | Deaktiviert automatische Update-Prüfung |
| `/nolog` | Keine Datenbankverbindung — nur Syntax-Parser |
| `WHENEVER SQLERROR EXIT FAILURE` | Bricht bei erstem Fehler ab, Exit-Code 1 |
| `SET FEEDBACK OFF` | Unterdrückt "1 row created." u. ä. |
| `SET TERMOUT OFF` | Unterdrückt Script-Output |
| `SET HEADING OFF` | Unterdrückt Spaltenüberschriften bei SELECT |

### In CI/CD-Pipelines

```bash
# Stufe 1: SQLPlus-Kompatibilität
sqlplus-checker ./scripts/ --no-warnings
if [ $? -ne 0 ]; then echo "sqlplus-checker fehlgeschlagen"; exit 1; fi

# Stufe 2: Oracle-Syntaxprüfung
docker run --rm \
  -v "$(pwd)/scripts":/scripts \
  container-registry.oracle.com/database/sqlcl:latest \
  /bin/bash -c '...'   # Kommando von oben
if [ $? -ne 0 ]; then echo "SQLcl-Syntaxprüfung fehlgeschlagen"; exit 1; fi
```

> **Hinweis:** SQLcl mit `/nolog` kann PL/SQL-Blöcke syntaktisch prüfen, aber
> keine Referenzen auf Objekte (Tabellen, Typen) auflösen — dafür wäre eine
> echte Datenbankverbindung nötig.

---

## Tests ausführen

```bash
uv run pytest test_main.py -v
```
