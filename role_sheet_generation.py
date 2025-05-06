#!/usr/bin/env python3
"""
Scan every Jira project, collect roles that contain at least one *direct* user,
and write a CSV:

project_key,role_name,role_url,usernames

`usernames` is a semicolon‑separated list of the user *names* in that role.
"""

import csv, os, requests, urllib3
from getpass import getpass

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

def main():
    jira   = input("Jira URL (e.g. https://jira.company.com): ").strip().rstrip("/")
    adm    = input("Admin username: ").strip()
    pw     = getpass("Admin password: ")
    outcsv = input("Output CSV path [roles_all_projects.csv]: ").strip() or "roles_all_projects.csv"

    sess = requests.Session()
    sess.auth, sess.verify = (adm, pw), False

    projects = sess.get(f"{jira}/rest/api/2/project").json()
    print(f"🔍 {len(projects)} project(s) found")

    os.makedirs(os.path.dirname(outcsv) or ".", exist_ok=True)
    total_written = 0
    with open(outcsv, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["project_key", "role_name", "role_url", "usernames"])   # <- new column

        for p in projects:
            key = p.get("key")
            roles_map = sess.get(f"{jira}/rest/api/2/project/{key}/role").json()
            for rname, rurl in roles_map.items():
                r = sess.get(rurl).json()
                actors = [a for a in r.get("actors", []) if a.get("type") == "atlassian-user-role-actor"]
                if not actors:
                    continue
                names = ";".join(a["name"] for a in actors)                 # <- collect users
                w.writerow([key, rname, rurl, names])
                total_written += 1
                print(f"  • {key}/{rname} ({len(actors)} user(s))")

    print(f"✅ CSV written: {outcsv}  (roles with users: {total_written})")

if __name__ == "__main__":
    main()
