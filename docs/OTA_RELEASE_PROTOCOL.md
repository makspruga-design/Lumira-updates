# Lumira public OTA release protocol

This PUBLIC repository is the verification/publication side of Lumira's in-game updater. It must never become a copy of the private game source.

## Canonical repositories

- PRIVATE source: `makspruga-design/Creature-rpg-2d`
- PUBLIC OTA channel: `makspruga-design/Lumira-updates`
- Native updater id: `lumira-017`
- Native engine line: Godot 4.3

## PRIMARY PUBLIC ROUTE — GENERIC RENDER IMPORT

For normal content/script updates, PRIVATE source is tested/built on Render first. This PUBLIC repo then imports only the signed candidate.

Canonical sequence, proven by **Lumira 0.19.7**:

1. PRIVATE source is changed and tested with real Godot 4.3 on Render.
2. PRIVATE Render OTA builder compiles only changed resources and signs cumulative Android/Windows manifests.
3. PUBLIC `release/import-render.request.json` states the expected version and Render candidate URL.
4. `.github/workflows/import-render-candidate.yml` downloads the candidate directly from Render.
5. Import workflow verifies the candidate's internal archive SHA-256.
6. Import workflow runs `tools/publish.py` with `LUMIRA_VERIFY_ONLY=1`, verifying signatures/resources with the public key.
7. Only after verification succeeds is the candidate promoted to `release/payload.json`.
8. Standard `.github/workflows/publish.yml` performs the full verification/publication step and creates the GitHub Release.
9. Confirm Android + Windows manifests and all new resource packs exist before telling the user to update.

Note: a commit pushed by a workflow using `GITHUB_TOKEN` does not automatically trigger another workflow. If `publish.yml` does not start after the import commit, use an explicit safe trigger/manual dispatch rather than assuming publication occurred.

## Proven reference: Lumira 0.19.7

- Feature: scheduled real ferry and airship travel.
- PRIVATE Render/Godot regression: **71 checks; 0 failures**.
- Signed candidate produced by the persistent Render builder.
- Generic Render importer downloaded the candidate and its public verifier step completed successfully.
- Standard public publisher completed successfully.
- Public tag: `v0.19.7`.
- Assets include `lumira-017-android.json`, `lumira-017-windows.json`, and the new compiled transport resource packs.

This is now the first reference future chats/agents should copy.

## Earlier references

- **0.19.5:** scene-only minimal/stub hotfix; useful only for tiny safe scene/resource fixes.
- **0.19.6:** established Render-first private script/content build with Creature Motion V4; Godot regression 237 checks / 0 failures.

## Security boundary

Never commit private game source, source archives, signing keystores, PATs/tokens, or private credentials here.

Allowed PUBLIC contents include:

- compiled content resource packs,
- signed Android/Windows manifests/candidates,
- SHA-256/size metadata,
- `update-public.pem`,
- release import/verification/publication tooling.

The private signing key stays in the PRIVATE source/build environment. This repo verifies signatures with the public key only.

## Publication contract

A candidate in `release/payload.json` is not trusted merely because it exists. `tools/publish.py` must verify signatures/resources before a release is published.

After success, always confirm the latest release contains:

- `lumira-017-android.json`
- `lumira-017-windows.json`
- every newly referenced resource pack

The game mounts pending content on startup, so the user must fully restart the game after installation.

## Agent/chat instruction

Before creating a Lumira update:

1. Read PRIVATE `docs/OTA_RELEASE_PLAYBOOK.md`.
2. Prefer Render/Godot 4.3 validation immediately; do not wait for broken private runners.
3. Build/sign in PRIVATE Render infrastructure.
4. Use the generic PUBLIC `import-render-candidate.yml` bridge.
5. Require successful public verification and publication.
6. Do not tell the user the update is available until the public release and platform manifests are actually present.
7. Do not default to a full APK unless the requested change is native/protected.
