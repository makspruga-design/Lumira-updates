# Lumira public OTA release protocol

This PUBLIC repository is the publication/verification side of Lumira's in-game updater. It must never become a copy of the private game source.

## Canonical repositories

- PRIVATE source: `makspruga-design/Creature-rpg-2d`
- PUBLIC OTA channel: `makspruga-design/Lumira-updates`
- Native updater id: `lumira-017`
- Current native engine line: Godot 4.3

## Current canonical release route

The preferred path for normal content/script updates is now **Render-first**, proven by Lumira 0.19.6:

1. Change is made and committed in the PRIVATE source repository.
2. Real Godot 4.3 regression is executed on Render/private build infrastructure.
3. Dedicated Render builder compiles/exports the changed OTA resources.
4. Android/Windows manifests are signed with PRIVATE signing material outside this PUBLIC repository.
5. Only compiled packs + signed manifests/candidate are transferred here.
6. `tools/publish.py` independently verifies signatures and every referenced resource.
7. The release is published atomically only after verification succeeds.
8. Latest release must contain both `lumira-017-android.json` and `lumira-017-windows.json`.

Do not wait on private GitHub Actions when they fail before provisioning a runner. Runner-less jobs are not test results.

## Proven reference: Lumira 0.19.6

0.19.6 established the preferred workflow for gameplay script/content updates:

- Creature Motion V4 was tested by real Godot 4.3 on Render.
- Runtime regression result: **237 checks; 0 failures**.
- The source PR was merged only after the real Godot run.
- A dedicated Render builder created the signed OTA candidate.
- This PUBLIC repo verified and published `v0.19.6`.
- Release contains both Android and Windows manifests.

Future chats/agents should copy this pattern first for normal OTA updates.

## Security boundary

Never commit private game source, source archives, signing keystores, PATs/tokens, or other private credentials here.

This repo may contain:

- compiled content resources,
- signed Android/Windows manifests,
- SHA-256/size metadata,
- the public verification key (`update-public.pem`),
- release verification/publication tooling.

The private signing key stays in the PRIVATE source/build environment. This repo verifies signatures with the public key only.

## Standard publication contract

A candidate placed at `release/payload.json` is not trusted merely because it exists. `.github/workflows/publish.yml` runs `tools/publish.py`, which must verify the candidate signatures/resources before publishing the GitHub Release.

After success, always verify the new latest release contains:

- `lumira-017-android.json`
- `lumira-017-windows.json`
- every newly referenced resource pack

The game mounts a downloaded pending content update on restart, so users must fully restart the game after installation.

## Special fallback reference: 0.19.5

0.19.5 used a scene-only minimal/stub compile because private GitHub Actions were not provisioning runners. That technique remains valid only for tiny resource/scene hotfixes that can be compiled without exposing private gameplay source.

Reference implementation retained here:

- `tools/manual_0195.py`
- `.github/workflows/manual-0195.yml`
- `release/manual-0195-*.json`

For changed gameplay scripts, prefer the 0.19.6 Render-first private build route instead.

## Agent/chat instruction

Before creating a Lumira update:

1. Read PRIVATE `docs/OTA_RELEASE_PLAYBOOK.md`.
2. Inspect the current PRIVATE `main`, `updates/release.json`, and latest PUBLIC release.
3. Prefer Render/Godot 4.3 validation + Render OTA builder immediately.
4. Do not spend time waiting for private GitHub Actions if no runner is provisioned.
5. Preserve the cumulative signed OTA model.
6. Do not default to a full APK unless the change is native/protected.
7. Do not tell the user an update is available until the public release and platform manifests are actually present.
