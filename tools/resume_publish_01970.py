#!/usr/bin/env python3
"""Finish one verified 0.19.70 draft, without creating or replacing anything.

The original publisher remains byte-for-byte unchanged and must finish its full
verify-only run before any authenticated GitHub request. This recovery script
accepts exactly the already signed payload and the already existing release ID.
"""
from __future__ import annotations

import argparse
import base64
import datetime
import email.utils
import io
import json
import os
from pathlib import Path
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile

import publish_resources as trusted

REPO = "makspruga-design/Lumira-updates"
API = "https://api.github.com/repos/" + REPO
RELEASE_ID = 429119806
PREVIOUS_LATEST_ID = 427009407
TAG = "v0.19.70"
VERSION = "0.19.70"
VERSION_CODE = 107
SOURCE_COMMIT = "c73537562e05c760c0aff643d2c3558411894de8"
PAYLOAD_SHA256 = "8d8fb035856d89f599a2757dc2d1f5ed3817f195a22211ee4cba9917e87d957d"
PAYLOAD_SIZE = 26367506
ARCHIVE_SHA256 = "b4035c640ab8fbb44f8fbc0a887c54a42e4893235180fe223ed4b77303f7b749"
ARCHIVE_SIZE = 19775450
VERIFIER_SHA256 = "13d3ecb09631fdea39398dfc4484056bb0ca2b3c548609402e090750ff034904"
PUBLIC_KEY_SHA256 = "99b858157199c870842c28a99461bbae89b857fe7788e7218cd3f774db70bad4"
VERIFIER_HELPER_SHA256 = "93cc2effb2a997078e7d53dbd48dc424b22b891748592ce2c6ba36724ee99c85"
MANIFESTS = {"lumira-017-android.json", "lumira-017-windows.json"}
ASSET_COUNT = 311
RELEASE_URL = API + "/releases/" + str(RELEASE_ID)
ASSET_URL = RELEASE_URL + "/assets"
UPLOAD_URL = "https://uploads.github.com/repos/" + REPO + "/releases/" + str(RELEASE_ID) + "/assets"
RETRY_DELAYS = (1, 2, 4)
MIN_UPLOAD_INTERVAL = 1.05
MAX_RETRY_AFTER = 60
INCOMPLETE_ASSET_ID = 692351833
INCOMPLETE_ASSET_NAME = "4ab5df34d109516166e9f139568038d98376e12120e953974da6061c00755af3.zip"
INCOMPLETE_ASSET_SIZE = 5469
INCOMPLETE_ASSET_TIME = "2026-10-30T14:20:51Z"
INCOMPLETE_ASSET_URL = API + "/releases/assets/" + str(INCOMPLETE_ASSET_ID)
CLEANUP_RECEIPTS: list[dict] = []
require = trusted.require
sha = trusted.sha


def inspect_payload(raw: bytes) -> tuple[dict, dict[str, bytes]]:
    require(len(raw) == PAYLOAD_SIZE and sha(raw) == PAYLOAD_SHA256,
            "This recovery accepts only the exact signed 0.19.70 payload")
    payload = json.loads(raw)
    require(payload.get("version") == VERSION and payload.get("source_commit") == SOURCE_COMMIT
            and payload.get("archive_sha256") == ARCHIVE_SHA256, "Payload identity differs")
    archive = base64.b64decode(payload["archive_base64"], validate=True)
    require(len(archive) == ARCHIVE_SIZE and len(archive) < 64 * 1024 * 1024
            and sha(archive) == ARCHIVE_SHA256, "Pinned archive differs")
    with zipfile.ZipFile(io.BytesIO(archive)) as outer:
        names = outer.namelist()
        require(len(names) == ASSET_COUNT and len(set(names)) == ASSET_COUNT
                and MANIFESTS <= set(names), "Unexpected archive asset set")
        require(sum(info.file_size for info in outer.infolist()) < 64 * 1024 * 1024,
                "Expanded archive exceeds unchanged transfer limit")
        require(all(name in MANIFESTS or re.fullmatch(r"[0-9a-f]{64}\.zip", name)
                    for name in names), "Unexpected archive filename")
        assets = {name: outer.read(name) for name in names}
    for name, body in assets.items():
        if name not in MANIFESTS:
            require(sha(body) + ".zip" == name, "Content-addressed ZIP differs")
    for platform in ("android", "windows"):
        raw_envelope = assets["lumira-017-" + platform + ".json"]
        require(len(raw_envelope) <= 1048576, "Envelope exceeds native limit")
        envelope = json.loads(raw_envelope)
        document = json.loads(base64.b64decode(envelope["payload"], validate=True))
        require(document.get("protocol") == 1 and document.get("native_id") == "lumira-017"
                and document.get("engine") == "4.3" and document.get("platform") == platform
                and document.get("version") == VERSION and document.get("version_code") == VERSION_CODE,
                "Signed manifest identity differs")
        final_refs = {item["sha256"] + ".zip" for item in document["packs"]
                      if item.get("url", "").startswith(trusted.PREFIX + TAG + "/")}
        require(final_refs == set(assets) - MANIFESTS, "Final-tag resources differ from archive")
    return payload, assets


def retryable(error: BaseException) -> bool:
    if isinstance(error, urllib.error.HTTPError):
        return error.code in (404, 429) or 500 <= error.code <= 599
    return isinstance(error, urllib.error.URLError)


def retry_delay(error: BaseException, base: int) -> float:
    delay = float(base)
    if isinstance(error, urllib.error.HTTPError) and (error.code == 429 or 500 <= error.code <= 599):
        value = error.headers.get("Retry-After") if error.headers else None
        if value:
            try:
                seconds = float(value)
            except ValueError:
                try:
                    when = email.utils.parsedate_to_datetime(value)
                    seconds = (when - datetime.datetime.now(datetime.timezone.utc)).total_seconds()
                except (TypeError, ValueError, OverflowError):
                    seconds = 0
            require(seconds <= MAX_RETRY_AFTER,
                    "Server requests a longer pause than this bounded recovery; rerun later")
            delay = max(delay, seconds)
    return delay


def safe_error(error: BaseException) -> str:
    if isinstance(error, urllib.error.HTTPError):
        return "GitHub HTTP " + str(error.code)
    if isinstance(error, urllib.error.URLError):
        return "GitHub network request failed"
    return type(error).__name__ + ": " + str(error)


def emit(event: str, **values) -> None:
    print(json.dumps({"event": event, **values}, separators=(",", ":")), flush=True)


def read_request(url: str, request, sleep):
    for attempt in range(len(RETRY_DELAYS) + 1):
        try:
            return request("GET", url)
        except (urllib.error.HTTPError, urllib.error.URLError) as error:
            if not retryable(error) or attempt == len(RETRY_DELAYS):
                raise
            delay = retry_delay(error, RETRY_DELAYS[attempt])
            emit("read_retry", http_status=getattr(error, "code", None), attempt=attempt + 2,
                 delay_seconds=delay)
            sleep(delay)


def checked_release(release: dict, *, draft: bool | None = None) -> None:
    require(isinstance(release, dict) and type(release.get("id")) is int
            and release["id"] == RELEASE_ID and release.get("tag_name") == TAG,
            "Unexpected release identity; refusing any other draft")
    require(release.get("prerelease") is False and type(release.get("draft")) is bool,
            "Unexpected release state")
    require(release.get("url") == RELEASE_URL and release.get("assets_url") == ASSET_URL
            and release.get("upload_url") == UPLOAD_URL + "{?name,label}",
            "Unexpected release API or upload endpoint")
    if draft is not None:
        require(release["draft"] is draft, "Release state changed during recovery")


def checked_assets(existing: dict, assets: dict[str, bytes]) -> None:
    require(set(existing) <= set(assets), "Draft contains unexpected assets; nothing will be replaced")
    ids = set()
    for name, item in existing.items():
        require(isinstance(item, dict) and item.get("name") == name and item.get("state") == "uploaded"
                and type(item.get("size")) is int and item["size"] == len(assets[name])
                and item.get("digest") == "sha256:" + sha(assets[name]),
                "Immutable existing asset differs or is incomplete: " + name)
        require(type(item.get("id")) is int and item["id"] > 0 and item["id"] not in ids,
                "Duplicate or invalid asset identity")
        ids.add(item["id"])


def read_assets(assets: dict[str, bytes], request, sleep) -> dict:
    result = {}
    for page in range(1, 5):
        rows = read_request(ASSET_URL + "?per_page=100&page=" + str(page), request, sleep)
        require(isinstance(rows, list) and len(rows) <= 100, "Malformed release asset page")
        for item in rows:
            require(isinstance(item, dict) and isinstance(item.get("name"), str)
                    and item["name"] not in result, "Duplicate or invalid asset name")
            result[item["name"]] = item
        checked_assets(result, assets)
        if len(rows) < 100:
            return result
    raise ValueError("Draft asset pagination exceeds the pinned 311-file release")


def checked_latest(latest: dict, release_id: int) -> None:
    require(isinstance(latest, dict) and latest.get("id") == release_id
            and latest.get("tag_name") == (TAG if release_id == RELEASE_ID else "v0.19.69")
            and latest.get("draft") is False and latest.get("prerelease") is False,
            "Latest release changed unexpectedly; refusing to advance it")


def _delete_placeholder_url(url: str) -> None:
    """One authorized repository asset DELETE; no redirect, JSON or retry."""
    require(re.fullmatch(re.escape(API + "/releases/assets/") + r"[1-9][0-9]*", url) is not None,
            "Unexpected placeholder DELETE endpoint")
    require(os.environ.get("GH_REPO") == REPO and bool(os.environ.get("GH_TOKEN")),
            "Placeholder cleanup requires this public repository's workflow token")
    request = urllib.request.Request(url, method="DELETE", headers={
        "Authorization": "Bearer " + os.environ["GH_TOKEN"],
        "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"})

    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            raise ValueError("Unexpected placeholder DELETE redirect")

    with urllib.request.build_opener(NoRedirect).open(request, timeout=120) as response:
        require(response.status == 204, "Unexpected placeholder DELETE response; require readback")


def delete_known_incomplete_placeholder() -> None:
    _delete_placeholder_url(INCOMPLETE_ASSET_URL)


def delete_current_attempt_placeholder(asset_id: int) -> None:
    require(type(asset_id) is int and asset_id > 0 and asset_id != INCOMPLETE_ASSET_ID,
            "Invalid current-attempt placeholder identity")
    _delete_placeholder_url(API + "/releases/assets/" + str(asset_id))


def read_asset_metadata(url: str, request, sleep):
    def metadata_or_absent(method, url):
        try:
            return request(method, url)
        except urllib.error.HTTPError as error:
            if error.code == 404:
                return None
            raise
    return read_request(url, metadata_or_absent, sleep)


def read_known_placeholder(request, sleep):
    return read_asset_metadata(INCOMPLETE_ASSET_URL, request, sleep)


def cleanup_membership(request, sleep) -> dict:
    rows_by_name, ids = {}, set()
    for page in range(1, 5):
        rows = read_request(ASSET_URL + "?per_page=100&page=" + str(page), request, sleep)
        require(isinstance(rows, list) and len(rows) <= 100, "Malformed cleanup membership page")
        for item in rows:
            require(isinstance(item, dict) and isinstance(item.get("name"), str)
                    and item["name"] not in rows_by_name and type(item.get("id")) is int
                    and item["id"] > 0 and item["id"] not in ids,
                    "Duplicate or invalid cleanup membership row")
            rows_by_name[item["name"]] = item
            ids.add(item["id"])
        if len(rows) < 100:
            return rows_by_name
    raise ValueError("Cleanup membership exceeds the pinned release")


def delete_once_confirm_absent(item: dict, *, origin: str, request, sleep, delete) -> dict:
    receipt = {"origin": origin, "placeholder_id": item["id"], "asset_name": item["name"],
               "created_at": item["created_at"], "cleanup_action": "delete_attempted_unconfirmed",
               "delete_attempted": True, "delete_acknowledged_204": False,
               "deleted_incomplete_placeholder_id": None,
               "uploaded_assets_deleted_or_overwritten": False}
    CLEANUP_RECEIPTS.append(receipt)
    try:
        delete()
        receipt["delete_acknowledged_204"] = True
        receipt["deleted_incomplete_placeholder_id"] = item["id"]
    except (urllib.error.HTTPError, urllib.error.URLError, OSError, ValueError) as error:
        receipt["delete_response_http_status"] = getattr(error, "code", None)
        emit("placeholder_delete_response_uncertain", http_status=getattr(error, "code", None),
             asset_id=item["id"])
    # A lost response never authorizes another DELETE. GET 404 is the only
    # accepted absence proof, including after an acknowledged 204.
    receipt["absence_confirmed_by_asset_get_404"] = False
    identity_fields = ("id", "name", "url", "size", "state", "digest",
                       "created_at", "updated_at", "download_count")
    for attempt in range(len(RETRY_DELAYS) + 1):
        current = read_asset_metadata(item["url"], request, sleep)
        if current is None:
            receipt["absence_confirmed_by_asset_get_404"] = True
            break
        require(isinstance(current, dict) and current.get("state") == "starter"
                and all(current.get(key) == item.get(key) for key in identity_fields),
                "Placeholder changed after DELETE attempt; preserve it and stop without another DELETE")
        if attempt < len(RETRY_DELAYS):
            sleep(RETRY_DELAYS[attempt])
    require(receipt["absence_confirmed_by_asset_get_404"],
            "Placeholder absence was not confirmed after one DELETE; no second DELETE is allowed")
    receipt["cleanup_action"] = ("removed_incomplete_placeholder" if receipt["delete_acknowledged_204"]
                                 else "absence_confirmed_after_uncertain_delete")
    emit("incomplete_placeholder_cleanup", **receipt)
    return receipt


def checked_placeholder_identity(item: dict, assets: dict[str, bytes]) -> None:
    require(isinstance(item, dict) and type(item.get("id")) is int
            and item["id"] == INCOMPLETE_ASSET_ID and item.get("name") == INCOMPLETE_ASSET_NAME
            and type(item.get("size")) is int and item["size"] == INCOMPLETE_ASSET_SIZE
            and item.get("url") == INCOMPLETE_ASSET_URL,
            "Known incomplete placeholder identity differs; refuse cleanup")
    body = assets.get(INCOMPLETE_ASSET_NAME)
    require(body is not None and len(body) == INCOMPLETE_ASSET_SIZE
            and sha(body) + ".zip" == INCOMPLETE_ASSET_NAME,
            "Known placeholder does not match the pinned verified payload")


def checked_incomplete_placeholder(item: dict, assets: dict[str, bytes]) -> None:
    checked_placeholder_identity(item, assets)
    require(item.get("state") == "starter" and item.get("digest") is None
            and item.get("created_at") == INCOMPLETE_ASSET_TIME
            and item.get("updated_at") == INCOMPLETE_ASSET_TIME
            and type(item.get("download_count")) is int and item["download_count"] == 0,
            "Known placeholder is not the exact untouched incomplete upload; refuse cleanup")


def cleanup_known_placeholder(assets: dict[str, bytes], *, request, sleep,
                              delete=delete_known_incomplete_placeholder) -> dict:
    checked_release(read_request(RELEASE_URL, request, sleep), draft=True)
    # The ordinary strict reader is unchanged. This separate preflight permits
    # exactly one known incomplete row while validating every other asset.
    rows_by_name = cleanup_membership(request, sleep)
    others = {name: item for name, item in rows_by_name.items() if item["id"] != INCOMPLETE_ASSET_ID}
    checked_assets(others, assets)
    listed = next((item for item in rows_by_name.values() if item["id"] == INCOMPLETE_ASSET_ID), None)
    current = read_known_placeholder(request, sleep)
    base = {"origin": "known_original_failed_upload", "placeholder_id": INCOMPLETE_ASSET_ID, "delete_attempted": False,
            "deleted_incomplete_placeholder_id": None, "uploaded_assets_deleted_or_overwritten": False}
    if current is None:
        return {**base, "cleanup_action": "already_absent", "absence_confirmed_by_asset_get_404": True}
    require(listed is not None, "Known asset does not belong to the pinned draft; refuse cleanup")
    checked_placeholder_identity(listed, assets)
    checked_placeholder_identity(current, assets)
    if current.get("state") == "uploaded":
        checked_assets({INCOMPLETE_ASSET_NAME: current}, assets)
        return {**base, "cleanup_action": "preserved_exact_uploaded_asset"}
    checked_incomplete_placeholder(listed, assets)
    checked_incomplete_placeholder(current, assets)
    # Recheck state immediately before the single permitted destructive call.
    checked_release(read_request(RELEASE_URL, request, sleep), draft=True)
    current = read_known_placeholder(request, sleep)
    if current is None:
        return {**base, "cleanup_action": "already_absent", "absence_confirmed_by_asset_get_404": True}
    checked_placeholder_identity(current, assets)
    if current.get("state") == "uploaded":
        checked_assets({INCOMPLETE_ASSET_NAME: current}, assets)
        return {**base, "cleanup_action": "preserved_exact_uploaded_asset"}
    checked_incomplete_placeholder(current, assets)
    return delete_once_confirm_absent(current, origin=base["origin"], request=request, sleep=sleep, delete=delete)


def cleanup_failed_attempt_placeholder(name: str, assets: dict[str, bytes], before: dict,
                                        attempt_started: datetime.datetime, *, request, sleep,
                                        delete=delete_current_attempt_placeholder) -> dict | None:
    require(name in assets and name not in before, "Attempt asset existed before upload; refuse cleanup")
    before_ids = {item["id"] for item in before.values()}
    rows = cleanup_membership(request, sleep)
    checked_assets({key: item for key, item in rows.items() if key != name}, assets)
    listed = rows.get(name)
    if listed is None:
        return None
    if listed.get("state") == "uploaded":
        checked_assets({name: listed}, assets)
        return None
    require(type(listed.get("id")) is int and listed["id"] > 0
            and listed["id"] not in before_ids and listed["id"] != INCOMPLETE_ASSET_ID,
            "Failed-upload placeholder ID is not new to this attempt")
    asset_id = listed["id"]
    url = API + "/releases/assets/" + str(asset_id)

    def checked_current(item):
        require(isinstance(item, dict) and item.get("id") == asset_id and item.get("name") == name
                and item.get("url") == url and type(item.get("size")) is int
                and item["size"] == len(assets[name]), "Current-attempt placeholder identity differs")
        if item.get("state") == "uploaded":
            checked_assets({name: item}, assets)
            return False
        require(item.get("state") == "starter" and item.get("digest") is None
                and type(item.get("download_count")) is int and item["download_count"] == 0,
                "Current-attempt asset is not an untouched starter")
        try:
            created = datetime.datetime.strptime(item["created_at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=datetime.timezone.utc)
            updated = datetime.datetime.strptime(item["updated_at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=datetime.timezone.utc)
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError("Invalid current-attempt placeholder timestamp") from error
        require(attempt_started <= created <= updated <= datetime.datetime.now(datetime.timezone.utc),
                "Starter predates this upload attempt or has an invalid timestamp")
        return True

    require(checked_current(listed), "Unexpected current-attempt list state")
    checked_release(read_request(RELEASE_URL, request, sleep), draft=True)
    current = read_asset_metadata(url, request, sleep)
    if current is None:
        return {"origin": "current_failed_upload", "placeholder_id": asset_id,
                "cleanup_action": "already_absent", "delete_attempted": False,
                "absence_confirmed_by_asset_get_404": True}
    if not checked_current(current):
        return {"origin": "current_failed_upload", "placeholder_id": asset_id,
                "cleanup_action": "preserved_exact_uploaded_asset", "delete_attempted": False}
    receipt = delete_once_confirm_absent(current, origin="current_failed_upload", request=request,
                                        sleep=sleep, delete=lambda: delete(asset_id))
    receipt["attempt_started_at"] = attempt_started.strftime("%Y-%m-%dT%H:%M:%SZ")
    return receipt


def confirm_published_latest(request, sleep) -> dict:
    for attempt in range(len(RETRY_DELAYS) + 1):
        latest = read_request(API + "/releases/latest", request, sleep)
        if latest.get("id") == RELEASE_ID:
            checked_latest(latest, RELEASE_ID)
            return latest
        checked_latest(latest, PREVIOUS_LATEST_ID)
        require(attempt < len(RETRY_DELAYS), "Published release has not become latest; verify before rerunning")
        sleep(RETRY_DELAYS[attempt])
    raise ValueError("Latest release confirmation did not complete")


def resume_release(assets: dict[str, bytes], *, request=trusted.github_request,
                   sleep=time.sleep, clock=time.monotonic,
                   delete_placeholder=delete_known_incomplete_placeholder,
                   delete_new_placeholder=delete_current_attempt_placeholder) -> dict:
    require(len(assets) == ASSET_COUNT and MANIFESTS <= set(assets), "Pinned release needs all 311 assets")
    release = read_request(RELEASE_URL, request, sleep)
    checked_release(release)
    before = read_request(API + "/releases/latest", request, sleep)
    cleanup = {"cleanup_action": "skipped_published_release", "delete_attempted": False,
               "deleted_incomplete_placeholder_id": None, "uploaded_assets_deleted_or_overwritten": False}
    if release["draft"]:
        checked_latest(before, PREVIOUS_LATEST_ID)
        cleanup = cleanup_known_placeholder(assets, request=request, sleep=sleep, delete=delete_placeholder)
    existing = read_assets(assets, request, sleep)
    initial_count = len(existing)
    uploaded = recovered = retries = 0
    attempt_cleanups = []
    if release["draft"] is False:
        require(set(existing) == set(assets), "Published release is incomplete and immutable")
        checked_latest(before, RELEASE_ID)
        status = "already_published_verified"
    else:
        checked_latest(before, PREVIOUS_LATEST_ID)
        last_upload = None
        for name, body in sorted(assets.items()):
            if name in existing:
                continue
            for attempt in range(len(RETRY_DELAYS) + 1):
                if last_upload is not None:
                    sleep(max(0.0, MIN_UPLOAD_INTERVAL - (clock() - last_upload)))
                last_upload = clock()
                attempt_started = datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0)
                before_attempt = dict(existing)
                try:
                    result = request("POST", UPLOAD_URL + "?name=" + urllib.parse.quote(name, safe=""), body, True)
                except (urllib.error.HTTPError, urllib.error.URLError) as error:
                    # A failed response can follow a successful upload. Always
                    # re-read the exact draft and all asset pages before retry.
                    emit("upload_response_error", http_status=getattr(error, "code", None), asset_name=name,
                         attempt=attempt + 1, remaining=ASSET_COUNT - len(existing))
                    # Respect server-requested pauses before readback as well as
                    # before another POST. Long pauses stop this bounded job.
                    server_pause = retry_delay(error, 0)
                    if server_pause:
                        emit("readback_pause", http_status=getattr(error, "code", None),
                             delay_seconds=server_pause)
                        sleep(server_pause)
                    checked_release(read_request(RELEASE_URL, request, sleep), draft=True)
                    attempt_cleanup = cleanup_failed_attempt_placeholder(
                        name, assets, before_attempt, attempt_started, request=request, sleep=sleep,
                        delete=delete_new_placeholder)
                    if attempt_cleanup is not None:
                        attempt_cleanups.append(attempt_cleanup)
                    existing = read_assets(assets, request, sleep)
                    if name in existing:
                        recovered += 1
                        emit("upload_observed_after_error", asset_name=name, remaining=ASSET_COUNT - len(existing))
                        break
                    if not retryable(error) or attempt == len(RETRY_DELAYS):
                        raise
                    delay = max(0.0, RETRY_DELAYS[attempt] - server_pause)
                    retries += 1
                    emit("upload_retry", http_status=getattr(error, "code", None), asset_name=name, attempt=attempt + 2,
                         remaining=ASSET_COUNT - len(existing), delay_seconds=delay)
                    sleep(delay)
                    continue
                checked_assets({name: result}, {name: body})
                existing[name] = result
                uploaded += 1
                if len(existing) % 10 == 0 or len(existing) == ASSET_COUNT:
                    emit("upload_progress", verified_assets=len(existing), remaining=ASSET_COUNT - len(existing))
                break
        checked_release(read_request(RELEASE_URL, request, sleep), draft=True)
        existing = read_assets(assets, request, sleep)
        require(set(existing) == set(assets), "Draft is missing verified assets; cannot publish")
        checked_latest(read_request(API + "/releases/latest", request, sleep), PREVIOUS_LATEST_ID)
        try:
            release = request("PATCH", RELEASE_URL,
                              {"draft": False, "prerelease": False, "make_latest": "true"})
        except (urllib.error.HTTPError, urllib.error.URLError) as error:
            # Do not repeat a mutation with an uncertain outcome. A read-only
            # check can prove that this exact draft was already published.
            emit("publish_response_error", http_status=getattr(error, "code", None))
            release = read_request(RELEASE_URL, request, sleep)
            checked_release(release, draft=False)
        checked_release(release, draft=False)
        status = "resumed_and_published"
    after = confirm_published_latest(request, sleep)
    metadata = {name: {"sha256": sha(body), "size": len(body)} for name, body in sorted(assets.items())}
    return {"status": status, "release_id": RELEASE_ID, "tag": TAG,
            "initial_asset_count": initial_count, "asset_count": len(existing),
            "asset_bytes": sum(map(len, assets.values())), "uploaded_responses_verified": uploaded,
            "uploads_observed_after_error": recovered, "upload_retries": retries,
            "asset_metadata_sha256": sha(json.dumps(metadata, sort_keys=True, separators=(",", ":")).encode()),
            "remote_asset_verification": "GitHub SHA256 digest, size, name and uploaded state",
            "latest_release_id_before": before["id"], "latest_release_id_after": after["id"],
            "incomplete_placeholder_cleanup": cleanup,
            "deleted_incomplete_placeholder_id": cleanup["deleted_incomplete_placeholder_id"],
            "current_attempt_placeholder_cleanups": attempt_cleanups}


def execute(payload_path: Path, *, verify_only: bool = False,
            verifier=trusted.verify_with_unchanged_publisher, request=trusted.github_request,
            sleep=time.sleep, clock=time.monotonic) -> dict:
    CLEANUP_RECEIPTS.clear()
    expected = Path.cwd().resolve() / "release/payload.json"
    require(payload_path.resolve() == expected and not payload_path.is_symlink(),
            "Use the assembled release/payload.json in this public checkout")
    require(payload_path.stat().st_size == PAYLOAD_SIZE, "Pinned payload size differs")
    raw = payload_path.read_bytes()
    _, assets = inspect_payload(raw)
    require(trusted.REPO == REPO and trusted.API == API and trusted.VERIFIER_SHA256 == VERIFIER_SHA256
            and trusted.PUBLIC_KEY_SHA256 == PUBLIC_KEY_SHA256, "Trusted verifier helper identity differs")
    require(sha(Path(trusted.__file__).read_bytes()) == VERIFIER_HELPER_SHA256,
            "Existing public verifier helper changed; stop for review")
    verifier(raw)
    emit("unchanged_full_verifier_passed", verifier_sha256=VERIFIER_SHA256,
         payload_sha256=PAYLOAD_SHA256, assets=len(assets))
    result = {"status": "verified_only", "asset_count": len(assets)}
    if not verify_only:
        require(os.environ.get("GH_REPO") == REPO and bool(os.environ.get("GH_TOKEN")),
                "Recovery requires this public repository's workflow token")
        result = resume_release(assets, request=request, sleep=sleep, clock=clock)
    return {**result, "version": VERSION, "version_code": VERSION_CODE, "source_commit": SOURCE_COMMIT,
            "payload_sha256": PAYLOAD_SHA256, "archive_sha256": ARCHIVE_SHA256,
            "verifier_sha256": VERIFIER_SHA256, "public_key_sha256": PUBLIC_KEY_SHA256,
            "verifier_helper_sha256": VERIFIER_HELPER_SHA256,
            "original_publisher_changed": False, "release_created": False,
            "uploaded_assets_deleted_or_overwritten": False}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--payload", type=Path, default=Path("release/payload.json"))
    parser.add_argument("--report", type=Path, default=Path("resume-01970-receipt.json"))
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    try:
        report = execute(args.payload, verify_only=args.verify_only)
    except Exception as error:
        report = {"status": "failed", "release_id": RELEASE_ID, "tag": TAG,
                  "payload_sha256": PAYLOAD_SHA256, "archive_sha256": ARCHIVE_SHA256,
                  "error": safe_error(error), "placeholder_cleanup_receipts": CLEANUP_RECEIPTS,
                  "uploaded_assets_deleted_or_overwritten": False}
        args.report.write_text(json.dumps(report, indent=2) + "\n")
        emit("recovery_failed", **report)
        raise SystemExit(1) from None
    args.report.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, separators=(",", ":")), flush=True)


if __name__ == "__main__":
    main()
