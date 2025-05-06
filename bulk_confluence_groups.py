#!/usr/bin/env python3
"""
Bulk‑migrate Confluence groups from a CSV:
source_username,target_username

For each pair:
  1) GET  /rest/api/user/memberof?username=<source>
  2) For each group, PUT /rest/api/user/<target>/group/<groupName>

Stops after 600 rows to avoid runaway.
"""

import csv, os, urllib.parse, requests, urllib3
from getpass import getpass
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

MAX_ROWS = 600          # safety stop

# ───────────────────────────────────────────────────────────────────────────
def fetch_user_groups(session, base, user):
    """Return a list of group names the user belongs to (all pages)."""
    groups, start, limit = [], 0, 200
    while True:
        r = session.get(f"{base}/rest/api/user/memberof",
                        params={"username": user, "start": start, "limit": limit})
        if r.status_code != 200:
            raise RuntimeError(f"memberOf {user} fail: {r.status_code} {r.text}")
        data = r.json()
        results = data.get("results", [])
        groups.extend(g["name"] for g in results)
        if start + limit >= data.get("size", 0): break
        start += limit
    return groups

def add_user_to_group(session, base, target, group):
    """PUT target user into group. Returns True if added / already there."""
    url = f"{base}/rest/api/user/{urllib.parse.quote(target)}/group/{urllib.parse.quote(group)}"
    r = session.put(url)
    return r.status_code in (200, 204)

# ───────────────────────────────────────────────────────────────────────────
def main():
    print("🔑 Confluence Group Migration (CSV)")
    base = input("Confluence base URL (e.g. https://confluence.company.com): ").strip().rstrip("/")
    admin = input("Admin username: ").strip()
    pwd   = getpass("Admin password: ")
    csv_path = input("CSV path (source,target): ").strip()

    if not os.path.isfile(csv_path):
        print("❌ CSV not found:", csv_path); return

    session = requests.Session(); session.auth=(admin, pwd); session.verify=False

    with open(csv_path, newline='', encoding="utf-8") as f:
        reader = csv.reader(f)
        for idx, (source, target, *_ignored) in enumerate(reader, 1):
            if idx > MAX_ROWS:
                print("⚠️  Limit of", MAX_ROWS, "rows reached – stopping.")
                break
            source, target = source.strip(), target.strip()
            if not source or not target: continue

            print(f"\n🕵️  [{idx}] Migrating groups for {source} ➜ {target}")
            try:
                groups = fetch_user_groups(session, base, source)
            except Exception as e:
                print("   ❌ Could not fetch groups:", e); continue

            print("   Groups count:", len(groups))
            added = 0
            for g in groups:
                if add_user_to_group(session, base, target, g):
                    added += 1
                    print(f"   + {target} added to '{g}'")
                else:
                    print(f"   ⚠️  Failed to add to '{g}'")
            print(f"   ✅ Done – {added}/{len(groups)} groups processed.")

if __name__ == "__main__":
    main()
