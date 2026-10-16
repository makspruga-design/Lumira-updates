#!/usr/bin/env python3
import argparse
import base64
import hashlib
import io
import json
import os
from pathlib import Path
import zipfile

VERSION = "0.19.5"
VERSION_CODE = 42
NATIVE_ID = "lumira-017"
MAIN_RESOURCE = ".godot/exported/133200997/export-c45a301d49d5d6ba03c16fbfb8a0681e-main_3d.scn"
BASE = Path("release/base")
RELEASE = Path("release")
NOTES = (
    "Исправлен запуск Крыльев возврата и Столба сохранения: состояние возврата теперь "
    "создаётся вместе с главной сценой. Сохраняются все изменения 0.19.4."
)


def compact(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def b64(data):
    return base64.b64encode(data).decode("ascii")


def unb64(text):
    return base64.b64decode(text, validate=True)


def zip_entries(entries):
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for name, data in sorted(entries.items()):
            info = zipfile.ZipInfo(name, (1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            zf.writestr(info, data)
    return out.getvalue()


def load_request():
    request = json.loads((RELEASE / "manual-0195-request.json").read_text("utf-8"))
    if request.get("version") != VERSION or int(request.get("version_code", -1)) != VERSION_CODE:
        raise SystemExit("request version mismatch")
    source_commit = str(request.get("source_commit", "")).strip()
    if len(source_commit) != 40:
        raise SystemExit("invalid source_commit")
    return request


def prepare(compiled_path):
    request = load_request()
    compiled = Path(compiled_path).read_bytes()
    if len(compiled) < 200:
        raise SystemExit("compiled scene is unexpectedly small")

    pack = zip_entries({MAIN_RESOURCE: compiled})
    pack_hash = sha256(pack)
    pack_name = f"{pack_hash}.zip"
    pack_entry = {
        "sha256": pack_hash,
        "size": len(pack),
        "url": f"https://github.com/makspruga-design/Lumira-updates/releases/download/v{VERSION}/{pack_name}",
        "resource": MAIN_RESOURCE,
        "content_sha256": sha256(compiled),
    }

    payloads = {}
    digests = {}
    for platform in ("android", "windows"):
        doc = json.loads((BASE / f"{platform}-0.19.4-payload.json").read_text("utf-8"))
        if doc.get("native_id") != NATIVE_ID or doc.get("platform") != platform:
            raise SystemExit(f"base manifest mismatch for {platform}")
        doc["version"] = VERSION
        doc["version_code"] = VERSION_CODE
        doc["notes"] = NOTES
        replaced = False
        new_packs = []
        for old in doc.get("packs", []):
            if old.get("resource") == MAIN_RESOURCE:
                new_packs.append(pack_entry)
                replaced = True
            else:
                new_packs.append(old)
        if not replaced:
            raise SystemExit(f"main scene resource missing in {platform} base manifest")
        doc["packs"] = new_packs
        raw = compact(doc)
        payloads[platform] = b64(raw)
        digests[platform] = sha256(raw)

    staging = {
        "version": VERSION,
        "version_code": VERSION_CODE,
        "source_commit": request["source_commit"],
        "pack_name": pack_name,
        "pack_b64": b64(pack),
        "main_resource": MAIN_RESOURCE,
        "main_content_sha256": sha256(compiled),
        "payloads": payloads,
    }
    (RELEASE / "manual-0195-staging.json").write_bytes(compact(staging))
    (RELEASE / "manual-0195-digests.json").write_text(
        json.dumps({
            "version": VERSION,
            "source_commit": request["source_commit"],
            "android_sha256": digests["android"],
            "windows_sha256": digests["windows"],
            "pack_sha256": pack_hash,
            "main_content_sha256": sha256(compiled),
        }, ensure_ascii=False, indent=2) + "\n",
        "utf-8",
    )
    print(json.dumps({"prepared": True, "pack": pack_name, "digests": digests}))


def finalize():
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding

    request = load_request()
    staging = json.loads((RELEASE / "manual-0195-staging.json").read_text("utf-8"))
    signatures = json.loads((RELEASE / "manual-0195-signatures.json").read_text("utf-8"))
    if staging.get("version") != VERSION or staging.get("source_commit") != request["source_commit"]:
        raise SystemExit("staging/request mismatch")
    if signatures.get("version") != VERSION or signatures.get("source_commit") != request["source_commit"]:
        raise SystemExit("signatures/request mismatch")

    public_key = serialization.load_pem_public_key(Path("update-public.pem").read_bytes())
    manifests = {}
    for platform in ("android", "windows"):
        raw = unb64(staging["payloads"][platform])
        signature = unb64(signatures[f"{platform}_signature_b64"])
        public_key.verify(signature, raw, padding.PKCS1v15(), hashes.SHA256())
        doc = json.loads(raw)
        if doc.get("version") != VERSION or doc.get("platform") != platform:
            raise SystemExit(f"signed payload mismatch for {platform}")
        manifests[f"{NATIVE_ID}-{platform}.json"] = compact({
            "payload": b64(raw),
            "signature": b64(signature),
        })

    pack = unb64(staging["pack_b64"])
    pack_name = staging["pack_name"]
    if sha256(pack) != pack_name[:-4]:
        raise SystemExit("pack hash mismatch")
    archive = zip_entries({**manifests, pack_name: pack})
    candidate = {
        "version": VERSION,
        "source_commit": request["source_commit"],
        "source_run": "manual-0.19.5-scene-hotfix",
        "archive_sha256": sha256(archive),
        "archive_base64": b64(archive),
    }
    (RELEASE / "manual-0195-final-payload.json").write_bytes(compact(candidate))
    print(json.dumps({"finalized": True, "archive_sha256": candidate["archive_sha256"]}))


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)
    prep = sub.add_parser("prepare")
    prep.add_argument("compiled_scene")
    sub.add_parser("finalize")
    args = parser.parse_args()
    if args.cmd == "prepare":
        prepare(args.compiled_scene)
    else:
        finalize()


if __name__ == "__main__":
    main()
