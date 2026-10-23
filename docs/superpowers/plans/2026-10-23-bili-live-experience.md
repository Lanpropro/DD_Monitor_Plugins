# Bili Live Experience Implementation Plan

**Goal:** Implement the user's sorting, durable cache, notification and gift usability feedback on `codex/bili-live`, with generic host support only on `function/plugin-tile-extensions`.

**Architecture:** Follow host main's package/emote identity and drag behavior. Keep plugin-owned durable preferences and account/room-scoped catalog snapshots; show cached content promptly while revalidating permissions. Route failures through a generic host notification/login API. Preserve current gift flow restrictions and diagnose resources before changing scaling.

**Tech Stack:** Python, PySide6, requests, JSON, existing independent host/plugin repositories.

**Spec:** User request in this chat dated 2026-10-23; reference host `main` c3743fd. Plugin base 83ef743; host base 186808a.

## Constraints and review focus

- All tests use synthetic accounts/temp storage. Never read real cookies or send live danmaku/gifts.
- Keep receiving/rendering in the host; do not perform further built-in feature removal in this task.
- Keep existing grouped-gift fix and one-submit/account/room safeguards.
- Corrupt/unwritable cache must not prevent browsing; disk snapshots never authorize sending before revalidation.
- Preserve absent-room items when saving order; drag completion must not click/send an emote.
- Notifications include captured room identity and offer login only for credential failures; late stale results stay suppressed.
- Rows must remain aligned after tab changes, bag refresh, resize and scrolling; inspect actual high-DPI backing pixels.
- Commit each completed deliverable separately, staging explicit files only. Parent handles shared git index operations.

## Task 1: Host notification bridge (independent)

- [ ] Read main `_show_notice`, `_danmaku_send_notice`, and existing generic PluginContext lifecycle.
- [ ] Write failing synthetic API/lifecycle/login-action tests.
- [ ] Add generic notification and login bridge with teardown/thread guards on the specified host branch; document its capability.
- [ ] Provide plugin reporter module and its exact integration contract without editing composer/plugin entry files.
- [ ] Run host API and notification regression checks; parent reviews and commits host and plugin changes separately.

## Task 2: Persistent order and emote image reuse (independent)

- [ ] Read main EmoticonOrder and drag event handling, existing legacy disk image cache and decoded-memory reuse.
- [ ] Add persistent order service with stable identities, cross-room absent-item retention, atomic writes and isolated test injection.
- [ ] Enable tab and in-category drag sorting, shared-popup refresh and suppression of click after drag.
- [ ] Reuse existing plugin and legacy host image cache, coalesce concurrent image loads with bounded decoded memory.
- [ ] Tests prove refresh/reopen/restart order, malformed cache handling, no real storage access in normal tests, no repeated HTTP for cached images, and stale icon isolation.

## Task 3: Gift diagnosis, layout and DPI (independent)

- [ ] Inspect anonymous public metadata for 心动盲盒/九秋飞仙; preserve unsupported purchase restrictions and expose concrete reasons in UI.
- [ ] Reproduce bag grid alignment across rebuild/refresh/resize; fix root cause with real Qt regression.
- [ ] Inspect input resolution and static/animation scaling; retain enough backing pixels for current DPR and verify both paths.
- [ ] Add regression fixtures/tests, run all gift API/media/popup checks. No AI super-resolution unless original resources demonstrably require it; document any remaining resource limit.

## Task 4: Catalog persistence and integration (parent)

- [ ] Implement atomic, versioned, bounded catalog snapshots keyed by hashed account identity and room; never store cookies.
- [ ] On open render valid disk snapshot immediately, fetch current permissions in background, and keep stale snapshots non-sendable.
- [ ] Validate fresh fetches and persist only current successful responses; refresh failures preserve readable cached catalog.
- [ ] Connect composer text/emote/gift/read failures to reporter, carrying room ID/name and actionable login classification.
- [ ] Keep request/context cancellation and missing-host fallback covered by tests.

## Task 5: Verification and delivery (parent)

- [ ] Run baseline suites, each new failing regression before its fix, and full relevant suites after integration.
- [ ] Build ZIP, verify install/source consistency, run native Qt at DPR 1.5/2.25 and inspect actual screenshots/resources.
- [ ] Fresh independent whole-change review; address actionable findings and rerun affected tests.
- [ ] Update plugin documentation and host API/testing index. Commit all complete changes; report SHAs, verification limitations and remaining files.
