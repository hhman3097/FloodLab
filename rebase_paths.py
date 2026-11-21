"""Rebase archived runtime paths after moving the project, keeping originals."""
import argparse
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def rewrite(value, old, new):
    if isinstance(value, str):
        return value.replace(old + "/", new + "/")
    if isinstance(value, list):
        return [rewrite(v, old, new) for v in value]
    if isinstance(value, dict):
        return {k: rewrite(v, old, new) for k, v in value.items()}
    return value


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--from-root", default="/Users/frank/Desktop/AI4Science")
    args = parser.parse_args()
    old, new = args.from_root.rstrip("/"), str(ROOT)
    if old == new:
        print("Project is already at the recorded location.")
        return
    backups = ROOT / "relocation-originals"
    changes = []
    for path in (ROOT / "event/runs").rglob("*.json"):
        content = path.read_bytes()
        value = json.loads(content)
        updated = rewrite(value, old, new)
        if value == updated:
            continue
        backup = backups / path.relative_to(ROOT)
        backup.parent.mkdir(parents=True, exist_ok=True)
        if backup.exists():
            raise RuntimeError(f"Backup already exists; do not overwrite evidence: {backup}")
        backup.write_bytes(content)
        path.write_text(json.dumps(updated, ensure_ascii=False, indent=2))
        changes.append({"path": str(path.relative_to(ROOT)),
                        "original_sha256": hashlib.sha256(content).hexdigest(),
                        "rebased_sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
    (ROOT / "relocation.json").write_text(json.dumps(
        {"from": old, "to": new, "changes": changes,
         "note": "Only runtime JSON paths changed. Original evidence archived; "
                 "tools.jsonl and GeoPackage geometry unchanged."}, indent=2))
    print(f"Rebased {len(changes)} JSON files; originals retained in relocation-originals.")


if __name__ == "__main__":
    main()
