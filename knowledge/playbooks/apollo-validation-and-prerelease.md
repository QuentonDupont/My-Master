# Playbook: apollo validation, pre-release and pre-prod verification

How a TECH `[APOLLO]` change goes from the AI's draft PR to verified on pre-prod, to the standard agreed with the board owner. It expands steps 3 (Validate locally) and 6 (Pre-production verification) of the team's [AI-first working model](https://github.com/pomelofashion/jira-workflow/blob/master/docs/working-model.md) for `pomelofashion/apollo`. Written up from [TECH-16](https://pomelofashion.atlassian.net/browse/TECH-16) / [apollo#4886](https://github.com/pomelofashion/apollo/pull/4886) (Category Dashboard cover images, 5 Oct 2026), which went through every step below.

This lives in `knowledge/playbooks/`, not `knowledge/sops/`, on purpose: `corpus/index.py` indexes `knowledge/sops/*.md` for the Historian's ticket triage, and this is an engineering process, not a support resolution.

**No secrets here (invariant 7).** The [Access](#access) table names the access each step needs and who grants it. It never holds passwords, keys or tokens, and neither do PR bodies, Jira comments or commit messages.

## At a glance

| Step | TECH status | Do | Evidence |
| --- | --- | --- | --- |
| 1. Review the AI's PR | Dev Validation | Read the whole diff and the ticket. Check that every removed symbol has no other users and that shared helpers are reused, not copied. | Notes in the PR |
| 2. Prove it locally | Dev Validation | PHPUnit before/after, plus a headed Playwright harness for UI changes | Results table and screenshots |
| 3. Check the data | Dev Validation | Run the new SQL read-only against real data. Check the CDN/env values the code depends on. | Query results summary |
| 4. Fix what testing finds | Dev Validation | Failing test first, then the fix, as a follow-up commit on the same PR | Before/after table |
| 5. Hand over | In Review | Fill the PR's Evidence section, tick only what was done, mark it ready, request review | Green CI, or a named infra failure |
| 6. Pre-release | In Review → Pre-prod | Optional: a `vX.Y.Z.N` pre-release deployed to pre-prod before merge | Release link, deploy run |
| 7. Verify on pre-prod | Pre-prod | Run the PR's pre-prod checklist on pre-prod data, then tick it | Screenshots on the ticket |

## 1. Review the AI's PR

- Read the whole diff against the ticket's acceptance criteria. AI PRs can satisfy the tests and still miss a criterion. TECH-16's first commit left no-cover tiles showing a broken-image icon, which goes against acceptance criterion 3.
- Check that each removed function or variable has no other users (`git grep` across the module), and that each new call goes through an existing helper (`classes/helper/common.php::imageUrl()` in TECH-16).
- Check behaviour the change drops silently. TECH-16's first commit dropped the `?fit=clip&q=65&w=500` CDN resize, so legacy images loaded about 5× heavier.
- Example values in an AI PR (URLs, SKUs) can be made up. Check any you plan to reuse: TECH-16's example image URL returned 404.

## 2. Prove it locally

There is no local apollo stack (no Docker or MySQL on the Mac), so test the real code in pieces:

- **PHPUnit:** PHP 7.2 at `/opt/homebrew/bin/php` (`brew install shivammathur/php/php@7.2`). Work in a git worktree of the PR branch. **Copy `vendor/` into the worktree (`cp -Rc ~/pomelo/apollo/vendor ./vendor`), never symlink it**: Composer's autoloader resolves the symlink target, so the tests silently run the main checkout's code. Copy `.env` too. Run `php vendor/bin/phpunit --no-coverage <test file>`.
- **Before/after:** each new test must fail on the old code (`git checkout origin/master -- <file>`, run it, restore) and pass on the branch.
- **UI changes: headed Playwright against a harness** in `~/pomelo/qa-playwright` (example: `tests/support/apollo-category-dashboard-harness.php` + `tests/pr-apollo4886-category-dashboard-cover.spec.js`). A PHP built-in-server script serves the real `.tpl` snippet, the module JS and `sp/js` jQuery 1.7.2, jQuery UI and `apollo/library.js` (admin pages need `ajaxPostCall`) from the checkout under test, with a fake `Db` that returns fixture rows. Run master, the first PR commit and the fixed branch side by side on different ports (`git archive <ref> | tar -x` gives a clean snapshot), with the same spec. Look at the screenshots, not only the assertions.
- Note what the harness fakes: here, the database and the one-line controller mapping. Say so in the PR.
- Known local-only failures: `ToolsTest::test_scandir*` need `themes/sp/`, which a plain checkout doesn't have. `ToolsTest` also rewrites `.htaccess`, so run `git checkout -- .htaccess` before committing.
- zsh gotchas: `$B:c…` is read as a history modifier (write `${B}:path`), an unquoted `====` errors, and `set -- $var` doesn't word-split.

## 3. Check the data

Mocked tests only check the SQL text. Before merge, run the new query **read-only** against real data:

- Run it inside `START TRANSACTION READ ONLY`, then `ROLLBACK`. Use a read-only user where one exists.
- Check that the columns exist, that a join returns at most one row where the code assumes so (for example one `cover = 1` image per product and shop), the `EXPLAIN` and the timing on the real `LIMIT`, and real rows for the products the ticket names.
- **Pre-prod data differs from production.** TECH-16's example product 462610 exists on production but not on pre-prod. Pick pre-prod check products from the pre-prod database (TECH-16 used 461636, 461070 and 461945 in Pomelo › New Arrivals (88), `id_shop` 1).
- Check the configuration the code reads. TECH-16's `imageUrl()` reads `env('_CDN_MEDIA_SERVER_')` before `CDN_MEDIA_SERVER`. Pre-prod sets only `CDN_MEDIA_SERVER` (`https://cdn-pre-production.pmlo.co/`), so legacy URLs kept the same CDN host as master. New-scheme `pml_image.image_url` values are absolute CDN URLs, saved at upload time from `_CDN_MEDIA_SERVER_` (`AdminProductsController::uploadProductFiles`). About 80k pre-prod covers point at `cdn-images.italist.com` instead.
- Check URLs on the real CDN with `curl` (status and size), for master's URL and the PR's URL. Both `cdn.pomelofashion.com/product/...` and `/img/p/...` accept `?fit=clip&q=65&w=500`.
- If a DB tunnel or a command containing a pasted credential is blocked by the auto-mode classifier, don't route around it. Ask Quenton to run the query, or to approve the command with auto mode off.

## 4. Fix what testing finds

- Write the failing test first, against the PR's current head, then the fix. Keep the fix on the same PR as a follow-up commit, not a new PR, unless it's a separate change.
- Copy the sibling implementation. TECH-16's no-image fallback (`../img/admin/no_image.png`) is the one `brand-dashboard.js` already uses.
- **commitlint: header ≤ 72 characters** (hard error in apollo's `commitlint.config.js`), format `type(scope): [TECH-n] subject`. The AI's own commit headers can be longer; don't amend a pushed commit to fix one.
- No AI attribution in commits or PR text (board owner's rule).

## 5. Hand over

- PR body: fill **Evidence** with a before/after results table (real counts), what the harness faked, screenshot links, findings for review and "Not verified yet". GitHub can't take image uploads through the API, so attach screenshots to the Jira ticket and link the attachments.
- **Checklist honesty:** the AI PR template lists the pre-prod checks under "Before marking ready for review", but pre-prod only happens after merge or a pre-release. Leave those boxes unticked with a note ("covered locally in the harness; to verify on pre-prod after merge") and tick them in step 7. Candidate fix for the team: move them to an "After merge (Pre-prod)" list in the AI PR template.
- **CI only runs on `ready_for_review`.** Pushing to a PR that is already ready starts only the labeler. Toggle `gh pr ready --undo` then `gh pr ready` to re-run Build. Each toggle moves the TECH ticket In Review → Dev Validation → In Review through the bot.
- If Build is red, read which step failed before reporting. On 5 Oct 2026, every apollo Build failed at the Codecov upload (`SSL ... handshake failure` fetching the uploader, `fail_ci_if_error: true`) while PHPUnit passed (10,813 tests). Suresh authorized skipping it; a `ci/codecov-non-blocking` branch appeared the same day.
- Request review from Suresh (`sureshdhakal`) plus CODEOWNERS. The board moves to Pre-prod when the PR merges.

## 6. Pre-release to pre-prod (optional, before merge)

Team pattern, first used by wallop-t for TECH-17 (`v2.849.0.1`), then for TECH-16 (`v2.849.0.2`):

1. **Check what is on pre-prod.** Pre-prod is **one shared environment**. Look at the latest `deploy-dispatch-docker-apollo-pre-prod` run (`gh run list -R pomelofashion/apollo --workflow dispatch-docker.yml`). If a teammate's build is there, don't replace it without agreeing. Build a combined branch instead: start from their tag and merge yours (`prerelease/vX.Y.Z.N`), check that the merge is clean, and run both changes' tests. TECH-16 + TECH-17 merged cleanly; they touched different files.
2. **Publish a pre-release.** Version = the latest release plus a 4th number:
   ```
   gh release create vX.Y.Z.N -R pomelofashion/apollo --target <branch> --prerelease \
     --title vX.Y.Z.N --generate-notes --notes-start-tag vX.Y.Z
   ```
   Publishing runs **PRE-PRODUCTION APOLLO - BUILD** (about 3 minutes). It also runs the PRODUCTION build and the Slack announcement. Those only build artifacts; nothing reaches production without a production deploy dispatch.
3. **Deploy** once the pre-prod build is green:
   ```
   gh api repos/pomelofashion/apollo/dispatches -X POST \
     -f event_type=deploy-dispatch-docker-apollo-pre-prod \
     -f 'client_payload[app_name]=apollo' \
     -f 'client_payload[env_name]=apollo-ecs-pre-production' \
     -f 'client_payload[app_version]=X.Y.Z.N'
   ```
   The `deploy-dispatch-docker` run ends with "Deployment finished. Version updated to apollo-ecs-pre-production-X.Y.Z.N".
4. Tell whoever's build you replaced or combined with. After the next normal release replaces pre-prod, the `prerelease/...` branch can be deleted; the tag keeps the commit.

## 7. Verify on pre-prod

- Apollo admin on pre-prod: `https://apollo-pre-production.pmlo.co/sp/index.php?controller=<AdminController>` (the admin folder is `sp`). It is only reachable from the office network or VPN, and returns 403 elsewhere. A direct link shows an "Invalid token" page first; follow its link. Quenton logs in himself.
- Run the PR's pre-prod checklist with products picked from the pre-prod database (step 3). Check the `<img src>` / network tab where the change is about URLs.
- Tick the checklist boxes, attach screenshots to the ticket, and move the ticket on to QA / UAT.

## Access

What each step needs and where it comes from. Credentials are issued to a person, never stored here, and never pasted into chats, PRs or tickets. If one is pasted somewhere it shouldn't be, ask its owner to rotate it.

| Access | Used in | Source / notes |
| --- | --- | --- |
| GitHub write on `pomelofashion/apollo` (QuentonDupont: push + triage since 5 Oct 2026) | Steps 4–6: push follow-up commits, edit the PR, publish the pre-release, send the deploy dispatch | GitHub org team membership (Suresh) |
| Jira TECH space (cloudId `c5a2f770-cd5b-4a8a-b550-854c401f5508`) | Attach evidence, read ticket status | Atlassian account |
| Bastion SSH key (`~/.ssh/bastion-pomelo`) | Step 3: tunnel to RDS | DevOps |
| Production DB, read-only (`lambda-read`, replica endpoint) | Step 3 | DevOps; read-only by design. Used through the auto-mode classifier only with Quenton's go-ahead |
| Pre-prod DB (`pre-production-master.proxy-…:3306`, local tunnel port 3307) | Step 3, read-only checks only | DevOps. Quenton authorized read-only checks on 5 Oct 2026. The app user `be-apollo` can write, so always run checks inside `START TRANSACTION READ ONLY` |
| Pre-prod app environment (Consul/Vault) | Step 3: confirm env values such as `CDN_MEDIA_SERVER` | DevOps. Share only the keys you need, not the whole env |
| Apollo pre-prod admin login | Step 7 | Apollo admin account; Quenton logs in |
| Office network / VPN | Step 7 | IT |
