#!/usr/bin/env python3
"""Verify with the unchanged publisher, then upload only immutable art ZIPs.

The signed Android/Windows stage manifests are verifier input only. They never
become release assets. This prerelease cannot become the game's latest update.
No private/cross-repository credentials are accepted.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import zipfile

REPO = "makspruga-design/Lumira-updates"
PREFIX = "https://github.com/" + REPO + "/releases/download/"
API = "https://api.github.com/repos/" + REPO
VERIFIER_SHA256 = "4e616e66eeb7350e9ff8355764ee1e8289654e58e2c7c08b0ef16e1483420643"
PUBLIC_KEY_SHA256 = "99b858157199c870842c28a99461bbae89b857fe7788e7218cd3f774db70bad4"
MAX_ARCHIVE = 64 * 1024 * 1024
MAX_PAYLOAD = 90 * 1024 * 1024
MANIFESTS = {"lumira-017-android.json", "lumira-017-windows.json"}
ART_PREFIXES = ("assets/creature_cycles/", "assets/boss_cycles/")


def require(ok: bool, message: str) -> None:
    if not ok:
        raise ValueError(message)


def sha(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def read_payload(path: Path) -> bytes:
    """Read one file or its bounded, hashed sibling parts index, without writes."""
    root, path = Path.cwd().resolve(), path.resolve()
    require(path.is_relative_to(root / "release") and path.name == "payload.json", "Payload must be under this checkout's release directory")
    index = path.with_name("payload.parts.json")
    if not index.is_file():
        require(path.stat().st_size < MAX_PAYLOAD, "Payload exceeds transfer limit")
        return path.read_bytes()
    require(index.stat().st_size < 262144, "Parts index is too large")
    record = json.loads(index.read_bytes())
    require(type(record.get("size")) is int and 0 < record["size"] < MAX_PAYLOAD, "Invalid chunked payload size")
    parts = record.get("parts")
    require(isinstance(parts, list) and 0 < len(parts) <= 100, "Invalid chunk count")
    chunks, seen, total = [], set(), 0
    for part in parts:
        raw_path = part.get("path")
        require(isinstance(raw_path, str) and not Path(raw_path).is_absolute() and ".." not in Path(raw_path).parts and "\\" not in raw_path, "Unsafe chunk path")
        chunk = (root / raw_path).resolve()
        require(chunk.is_relative_to(path.parent / "parts") and chunk not in seen, "Chunk is outside this payload or repeated")
        size = part.get("size")
        require(type(size) is int and 0 < size <= 2 * 1024 * 1024 and chunk.stat().st_size == size, "Invalid chunk size")
        body = chunk.read_bytes()
        require(sha(body) == part.get("sha256"), "Chunk SHA256 mismatch")
        seen.add(chunk)
        total += len(body)
        require(total <= record["size"], "Chunks exceed declared payload size")
        chunks.append(body)
    body = b"".join(chunks)
    require(len(body) == record["size"] and sha(body) == record.get("sha256"), "Reassembled payload differs")
    require(json.loads(body)["version"] == record.get("version"), "Chunk index version differs")
    return body


def inspect_stage(payload_bytes: bytes, stage_tag: str) -> tuple[dict, dict[str, bytes]]:
    """Additional resource-only checks; RSA/full dependencies use publish.py."""
    require(len(payload_bytes) < MAX_PAYLOAD, "Payload exceeds transfer limit")
    payload = json.loads(payload_bytes)
    version = payload.get("version")
    require(isinstance(version, str) and re.fullmatch(r"\d+\.\d+\.\d+", version), "Invalid version")
    require(stage_tag == "v" + version + "-resources", "Wrong resource prerelease tag")
    require(re.fullmatch(r"[0-9a-f]{40}", str(payload.get("source_commit", ""))) is not None, "Missing source provenance")
    archive = base64.b64decode(payload["archive_base64"], validate=True)
    require(0 < len(archive) < MAX_ARCHIVE and sha(archive) == payload.get("archive_sha256"), "Archive SHA256/64 MiB limit mismatch")
    assets, referenced = {}, {}
    with zipfile.ZipFile(io.BytesIO(archive)) as outer:
        names = outer.namelist()
        require(len(names) == len(set(names)) and 2 < len(names) <= 2050, "Invalid archive entries")
        require(MANIFESTS <= set(names), "Both signed verification manifests are required")
        require(sum(info.file_size for info in outer.infolist()) < MAX_ARCHIVE,
                "Expanded staging transfer exceeds 64 MiB")
        for name in set(names) - MANIFESTS:
            require(re.fullmatch(r"[0-9a-f]{64}\.zip", name) is not None, "Only content-addressed ZIPs may be staged")
            body = outer.read(name)
            require(sha(body) + ".zip" == name, "Staged ZIP name/hash mismatch")
            with zipfile.ZipFile(io.BytesIO(body)) as inner:
                entries = inner.namelist()
                require(len(entries) == 1 and 0 < inner.infolist()[0].file_size <= 268435456
                        and inner.testzip() is None, "Staged ZIP is not a single bounded verified resource")
                resource = entries[0]
                require(resource.startswith(ART_PREFIXES) and resource.endswith(".webp") and ".." not in resource.split("/") and "\\" not in resource, "Only creature/boss raw art can be staged")
            assets[name] = body
            referenced[name] = resource
        previous_packset = None
        for platform in ("android", "windows"):
            envelope_bytes = outer.read("lumira-017-" + platform + ".json")
            require(len(envelope_bytes) <= 1048576, "Manifest exceeds native limit")
            envelope = json.loads(envelope_bytes)
            document = json.loads(base64.b64decode(envelope["payload"], validate=True))
            require(document.get("protocol") == 1 and document.get("native_id") == "lumira-017" and document.get("engine") == "4.3" and document.get("platform") == platform, "Wrong signed native identity")
            require(document.get("version") == version and type(document.get("version_code")) is int and document["version_code"] > 23, "Manifest version mismatch")
            require(isinstance(document.get("packs"), list) and len(document["packs"]) <= 2048, "Native pack limit exceeded")
            stage_items, resources, digests = {}, set(), set()
            total = 0
            for item in document["packs"]:
                resource, digest, size = item.get("resource"), item.get("sha256"), item.get("size")
                require(isinstance(resource, str) and resource not in resources and digest not in digests, "Duplicate resource or digest")
                require(type(size) is int and 0 < size <= 268435456, "Invalid individual pack size")
                require(isinstance(digest, str) and re.fullmatch(r"[0-9a-f]{64}", digest), "Invalid pack digest")
                resources.add(resource); digests.add(digest); total += size
                filename = digest + ".zip"
                if filename in assets:
                    require(item.get("url") == PREFIX + stage_tag + "/" + filename and resource == referenced[filename] and size == len(assets[filename]), "Staged resource URL/identity/size differs")
                    stage_items[filename] = resource
                else:
                    require(isinstance(item.get("url"), str) and not item["url"].startswith(PREFIX + stage_tag + "/"), "Missing staged archive resource")
                    require(not item["url"].startswith(PREFIX + "v" + version + "/"), "Stage verifier may not depend on the future playable release")
            require(total <= 2147483648, "Native total size limit exceeded")
            require(set(stage_items) == set(assets), "Unsigned or one-platform-only staging asset")
            require(previous_packset is None or stage_items == previous_packset, "Platforms disagree on staged resource identity")
            previous_packset = stage_items
    return payload, assets


def verify_with_unchanged_publisher(payload: bytes) -> None:
    verifier, public_key = Path("tools/publish.py"), Path("update-public.pem")
    require(sha(verifier.read_bytes()) == VERIFIER_SHA256, "Existing publisher verifier changed; stop for review")
    require(sha(public_key.read_bytes()) == PUBLIC_KEY_SHA256, "Native update trust key changed")
    with tempfile.TemporaryDirectory(prefix="lumira-resource-verifier-") as directory:
        work = Path(directory)
        (work / "release").mkdir(); (work / "tools").mkdir()
        (work / "release/payload.json").write_bytes(payload)
        shutil.copyfile(verifier, work / "tools/publish.py")
        shutil.copyfile(public_key, work / "update-public.pem")
        environment = dict(os.environ, GH_REPO=REPO, LUMIRA_VERIFY_ONLY="1")
        environment.pop("GH_TOKEN", None); environment.pop("GITHUB_TOKEN", None)
        subprocess.run([sys.executable, "-I", str(work / "tools/publish.py")], cwd=work, env=environment, check=True, timeout=1500)


def github_request(method: str, url: str, data=None, binary: bool = False):
    parsed = urllib.parse.urlsplit(url)
    release_path = "/repos/" + REPO + "/releases"
    require(parsed.scheme == "https" and parsed.netloc in ("api.github.com", "uploads.github.com")
            and (parsed.path == release_path or parsed.path.startswith(release_path + "/")),
            "Unexpected GitHub API target")
    token = os.environ["GH_TOKEN"]
    body = data if binary else json.dumps(data).encode() if data is not None else None
    request = urllib.request.Request(url, data=body, method=method, headers={
        "Authorization": "Bearer " + token, "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28", "Content-Type": "application/octet-stream" if binary else "application/json"})
    # These fixed repository endpoints have no legitimate cross-host redirect.
    # Refuse redirects so the workflow token cannot follow a response elsewhere.
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            raise ValueError("Unexpected GitHub API redirect")
    opener = urllib.request.build_opener(NoRedirect)
    with opener.open(request, timeout=120) as response:
        return json.load(response)


def release_assets(release_id: int, request=github_request) -> dict:
    result = {}
    for page in range(1, 22):
        rows = request("GET", API + "/releases/" + str(release_id) + "/assets?per_page=100&page=" + str(page))
        require(isinstance(rows, list), "Malformed release assets response")
        for row in rows:
            require(row["name"] not in result, "Duplicate remote asset name")
            result[row["name"]] = row
        if len(rows) < 100:
            return result
    raise ValueError("Remote resource asset count exceeds native limits")


def checked_existing(existing: dict, assets: dict[str, bytes]) -> None:
    require(set(existing) <= set(assets), "Resource prerelease contains unrelated assets or updater manifests")
    for name, metadata in existing.items():
        require(metadata.get("state") == "uploaded" and metadata.get("size") == len(assets[name]) and metadata.get("digest") == "sha256:" + sha(assets[name]), "Immutable existing asset differs: " + name)


def publish_stage(stage_tag: str, payload: dict, assets: dict[str, bytes], request=github_request) -> dict:
    require(bool(assets) and all(re.fullmatch(r"[0-9a-f]{64}\.zip", name) for name in assets), "Refusing to upload manifests/non-resource assets")
    before_latest = request("GET", API + "/releases/latest")
    require(before_latest.get("tag_name") != stage_tag, "Resource tag was unexpectedly latest")
    try:
        release = request("GET", API + "/releases/tags/" + urllib.parse.quote(stage_tag, safe=""))
    except urllib.error.HTTPError as error:
        if error.code != 404:
            raise
        release = None
    if release is None:
        release = request("POST", API + "/releases", {"tag_name": stage_tag, "target_commitish": "main",
            "name": "Lumira " + payload["version"] + " update resources",
            "body": "Immutable verified art resources for the separately signed game update. No updater manifests are published here.",
            "draft": True, "prerelease": True, "make_latest": "false"})
    require(release.get("tag_name") == stage_tag, "Unexpected release identity")
    existing = release_assets(release["id"], request)
    checked_existing(existing, assets)
    if not release.get("draft"):
        require(release.get("prerelease") is True and set(existing) == set(assets), "Published resource prerelease is immutable/incomplete")
    else:
        upload = release["upload_url"].split("{")[0]
        require(upload == "https://uploads.github.com/repos/" + REPO + "/releases/" + str(release["id"]) + "/assets",
                "Unexpected resource upload endpoint")
        for name, body in sorted(assets.items()):
            if name in existing:
                continue
            result = request("POST", upload + "?name=" + urllib.parse.quote(name, safe=""), body, True)
            checked_existing({name: result}, {name: body})
        existing = release_assets(release["id"], request)
        checked_existing(existing, assets)
        require(set(existing) == set(assets), "Draft did not receive every verified resource")
        release = request("PATCH", API + "/releases/" + str(release["id"]), {"draft": False, "prerelease": True, "make_latest": "false"})
    require(release.get("draft") is False and release.get("prerelease") is True, "Resource release is not a published prerelease")
    after_latest = request("GET", API + "/releases/latest")
    require(after_latest.get("id") == before_latest.get("id"), "Latest release changed during staging; stop before final rollout")
    return {"status": "resources_published_latest_unchanged", "tag": stage_tag, "release_id": release["id"],
        "source_commit": payload["source_commit"], "archive_sha256": payload["archive_sha256"],
        "asset_count": len(assets), "asset_bytes": sum(map(len, assets.values())),
        "assets": {name: {"size": len(body), "sha256": sha(body)} for name, body in sorted(assets.items())},
        "latest_release_id": after_latest["id"], "latest_tag": after_latest["tag_name"],
        "updater_manifests_uploaded": False, "verifier_sha256": VERIFIER_SHA256,
        "public_key_sha256": PUBLIC_KEY_SHA256}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--payload", type=Path, required=True)
    parser.add_argument("--stage-tag", required=True)
    parser.add_argument("--verify-only", action="store_true")
    parser.add_argument("--report", type=Path, default=Path("resource-staging-receipt.json"))
    args = parser.parse_args()
    require(os.environ.get("GH_REPO", REPO) == REPO, "Unexpected repository")
    raw = read_payload(args.payload)
    payload, assets = inspect_stage(raw, args.stage_tag)
    verify_with_unchanged_publisher(raw)
    if args.verify_only:
        print(json.dumps({"status": "verified_only_not_published", "tag": args.stage_tag, "asset_count": len(assets), "asset_bytes": sum(map(len, assets.values()))}))
        return
    require(bool(os.environ.get("GH_TOKEN")), "GitHub workflow token is missing")
    receipt = publish_stage(args.stage_tag, payload, assets)
    args.report.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({k: v for k, v in receipt.items() if k != "assets"}, ensure_ascii=False))


if __name__ == "__main__":
    main()
