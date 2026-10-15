# Lumira updates

Official signed content updates for Lumira on Android and Windows.

Install them from **Menu → Updates → Check → Download**, then restart the game.
The initial supported native version is **0.17.0**, native ID `lumira-017`.
Game saves remain separate from downloaded resource packages.

Releases contain signed platform manifests and compiled resources. The game
verifies RSA signatures, compatibility and every resource's SHA-256 before
installation. An unfinished first launch rolls back to the prior working version.

The private source project builds and tests updates. A verified signed release
payload is submitted to `release/payload.json` here; GitHub Actions checks it
again and publishes a release using this repository's own temporary token.
No persistent account token or private-source access is required here.

Not every source commit is a release. The release version and code must advance.
Previously published releases are immutable; keep old resource assets available.
Engine, bootstrap and native configuration changes require a new native build.

This repository does not grant a license to redistribute the game's artwork or code.
