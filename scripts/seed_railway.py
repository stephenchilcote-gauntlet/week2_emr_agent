"""Seed the Railway MySQL database with test patient data."""
import re
import sys
from urllib.parse import urlparse

import pymysql


def main():
    if len(sys.argv) < 2:
        print("Usage: seed_railway.py <MYSQL_PUBLIC_URL>")
        sys.exit(1)

    u = urlparse(sys.argv[1])
    conn = pymysql.connect(
        host=u.hostname, port=u.port, user=u.username, password=u.password, database="openemr"
    )
    cur = conn.cursor()

    with open("docker/seed_data.sql") as f:
        sql = f.read()

    # Strip comments and USE statement
    sql = re.sub(r"--.*$", "", sql, flags=re.MULTILINE)
    sql = re.sub(r"/\*.*?\*/", "", sql, flags=re.DOTALL)
    sql = sql.replace("USE openemr;", "")

    stmts = [s.strip() for s in sql.split(";") if s.strip()]
    for stmt in stmts:
        try:
            cur.execute(stmt)
            print(f"OK: {stmt[:70]}...")
        except Exception as e:
            print(f"ERR: {e}")

    conn.commit()

    # Verify
    cur.execute("SELECT pid, fname, lname FROM patient_data ORDER BY pid")
    print("\n=== All patients ===")
    for row in cur.fetchall():
        print(f"  pid={row[0]}: {row[1]} {row[2]}")

    cur.execute("SELECT COUNT(*) FROM lists WHERE id >= 90000")
    print(f"Seed diagnoses/meds: {cur.fetchone()[0]}")

    conn.close()
    print("\nSeed complete!")


if __name__ == "__main__":
    main()
