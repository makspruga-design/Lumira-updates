# Lumira public OTA release protocol

This PUBLIC repository is the publication/verification side of Lumira's in-game updater. It must never become a copy of the private game source.

## Canonical repositories

- PRIVATE source: `makspruga-design/Creature-rpg-2d`
- PUBLIC OTA channel: `makspruga-design/Lumira-updates`
- Native updater id: `lumira-017`
- Current native engine line: Godot 4.3

## Security boundary

Never commit private game source, source archives, signing keystores, PATs/tokens, or other private credentials here.

This repo may contain:

- compiled content resources,
- signed Android/Windows manifests,
- SHA-256/size metadata,
- the public verification key (`update-public.pem`),
- release verification/publication tooling.

The private signing key stays in the PRIVATE source environment. This repo verifies signatures with the public key only.

## Standard publication contract

A release candidate placed at `release/payload.json` is not trusted merely because it exists. `.github/workflows/publish.yml` runs `tools/publish.py`, which must verify the candidate signatures/resources before publishing the GitHub Release.

After success, always verify the new latest release contains:

- `lumira-017-android.json`,
- `lumira-017-windows.json`,
- every newly referenced resource pack.

The game mounts a downloaded pending content update on restart, so users must fully restart the game after installation.

## Proven fallback reference: 0.19.5

On 2026-10-16 private GitHub Actions could not reliably provision a runner. A scene-only hotfix was therefore prepared without exposing private game source:

1. The real fix was first committed in the PRIVATE source repo.
2. A public runner used Godot 4.3 to compile a minimal main scene containing an explicit `ReturnTravelState` node, with harmless stub scripts at matching resource paths.
3. The workflow instantiated the compiled scene and asserted that `ReturnTravelState`, `WorldRoot`, and `Player` were present.
4. Only that compiled main-scene resource replaced the corresponding entry from the previous cumulative manifest.
5. Android and Windows manifest payloads were signed outside the public trust boundary with the PRIVATE key.
6. This repo verified those signatures with `update-public.pem`.
7. The verified candidate was promoted to `release/payload.json`.
8. Standard `publish.yml` verification succeeded and published `v0.19.5`.
9. The user updated via the in-game updater, restarted, and confirmed the fix worked on the installed game.

Reference implementation files retained here:

- `tools/manual_0195.py`
- `.github/workflows/manual-0195.yml`
- `release/manual-0195-*.json`

They are a forensic/example implementation, not a template to reuse with the same version number. Future releases must use new monotonically increasing versions/codes.

## Important limitation

The public-runner minimal/stub technique is appropriate only for resources/scenes that can be compiled without publishing private gameplay source. Do not use it to claim that changed private gameplay scripts were compiled or tested. Script logic changes should be built in a private/local/self-hosted Godot environment, then flow through the same signed public verification/publish stage.

## Agent/chat instruction

Before creating a Lumira update, inspect the PRIVATE source playbook `docs/OTA_RELEASE_PLAYBOOK.md`, the latest PUBLIC release, the current `release/payload.json`, `tools/publish.py`, and the requested source commit/version. Preserve the cumulative signed OTA model. Do not default to a full APK unless the change is native/protected.
