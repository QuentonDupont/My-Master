---
name: apollo-category-nav
description: Use when changing Apollo's Category Navigation for any shop — adding or reordering nav items, setting banner images or text colours, replicating TH's blueprint to another market, or unlocking a shop from the default set. These are the operating lessons from doing it; CLAUDE.md's Apollo invariant (marketing_onsite surfaces only, never orders/customers/pricing/stock) still governs.
---

# Apollo category navigation — operating lessons

Category Navigation publishes live on save. There is no draft state for an
unlocked shop, so everything below assumes a customer sees the result within
one cache cycle. The recurring failure here is not "the change was wrong" —
it is "the UI said it saved and it did not". Verify against the database.

## The names lie — know which bar you are editing

`pml_navigation_bar_category.bar_type` has three values and only one of them
is the website:

- **`mobile`** — despite the name, this is the **live web navigation**, the
  flat list Apollo labels *"Backward Support Navigation"* behind the
  "Show old navigation bar" toggle. Confirmed by DOM inspection of the live
  storefront: it renders as `ul.categories-section`.
- **`product_line`** — the grouped Women / Men / Kids view, Apollo's
  *"new navigation bar for Product Line"*. Read by the app.
- **`web_top`** — a desktop mega-menu that does **not** render on the live
  site. Do not spend time on it unless someone confirms it is wanted.

Parent ids are stable across shops: `3271` = web nav, `3953` = Women,
`3954` = Men, `3955` = Kids. Scope every DOM lookup by parent — the same
category id exists in several bars at once, and an unscoped
`[data-category=X]` will happily match the wrong one.

Separately, Feeds → Feeds V3 has its own **"Product Line"** entry. It is a
different surface from `bar_type='product_line'` and is not touched by this
work. Do not conflate them.

## Two controls are automation-resistant; both have a URL

The multistore "Multistore configuration for" dropdown is Chosen.js and
ignores synthetic clicks entirely — no network request fires. Do not burn a
session on it. Navigate instead:

- Switch shop: append `&setShopContext=s-<id>` to the controller URL.
- View a locked shop's custom set: append `&view_custom=1`.

Shop ids: TH 1, SG 2, Global 4, ID 5, AU 10, MY 11, HK 12, PH 14, KH 15.

The page sometimes serves the previous shop's content on the first request
after switching; re-issue the navigation and confirm the shop name in the
"only for shop:" banner before trusting anything on screen.

## Locked shops, and what unlocking actually does

A shop using the shared "All shops" set shows *"it is locked up"*. In that
state edits are impossible and the admin list is the default set, not the
shop's own. `view_custom=1` reveals the shop's **custom** set, which can be
edited freely with no customer impact while the shop stays locked.

Three things to know before unlocking:

- The custom set may be **stale leftovers** from years ago, not a copy of
  what is live. Read it before assuming it is a sane starting point.
- "Clone default navigation set" replaces the entire custom set. Safe when
  the custom set is empty or junk; destructive otherwise.
- Unlock is the moment it goes live, and it is reversible — Apollo's own
  text says the shop can be locked back at any time. It still belongs to the
  human, not to you.

## Drive the forms by their fields, not their widgets

The category picker and several buttons are flaky under automation; the
underlying form is not. In `#div_select_category` (add) and
`#div_edit_category_navigation` (edit):

- `category_id` — hidden input, set it directly instead of using the picker
- `nav_title[N]` — one per language; fill **all** of them, otherwise the
  item renders blank in whichever locale you skipped
- `text_color` — `#ffffff` everywhere, `#f44336` for Sale
- Reordering: rearrange the `li.route` elements, then call the page's own
  `sortNavigationBarCategory(jQuery(ul))`

Language ids: 1 en-US, 2 Thai, 3 Singapore, 4 en-Global, 5 Indonesian,
10 Australian, 11 Malaysian, 12 Chinese, 13 en-PH, 14 en-HK, 15 en-KH.

## The two traps that corrupt data

**Stale `item_id` in the edit modal.** The form can display one category
while still holding the previous row's `item_id`. Applying then rewrites
*that* row and changes its category — silent corruption. Before every apply,
assert the form's `item_id` equals the row's `data-id`. This was caught once
only because the database was checked first.

**`parent=0` orphans.** Reusing an already-open add form saves rows with no
parent. They render on the storefront but are invisible in admin, cannot be
reordered, and cannot be given a banner. Always re-open Add Item for the
target group and confirm `parent_id` before adding. Fixing one means delete
and re-add — the server ignores `parent_id` on edit.

## Verify against the database, never the UI

Writes drop silently and report success. In one pass five of twenty-four
adds vanished; an ordering save reverted; two banner uploads did not land.
Every one was invisible in the UI and obvious in SQL.

Read-only tunnel (`lambda-read`, drops periodically, just restart it):

```
ssh -i ~/.ssh/bastion-pomelo -L 3306:production-master.cluster-ro-crnhkqqdwxzi.ap-southeast-1.rds.amazonaws.com:3306 ec2-user@ec2-47-129-57-49.ap-southeast-1.compute.amazonaws.com -N -o ExitOnForwardFailure=yes -o ServerAliveInterval=60
```

After any batch, check count, order, `parent`, duplicates and
`category_navigation_banner_image` per shop and parent — then fix and
re-check, rather than reporting from what the screen said.

## Banners

There is no URL field. A banner can only be set by uploading a file, and the
form carries no hidden value for the existing image, so it cannot be copied
between shops by editing text. To replicate TH's: read the CDN urls from
`pml_navigation_bar_category` for shop 1, `curl` them down, and upload each
one per shop. Files are WebP behind a `.jpg` name; the upload accepts them.

The path `/img/nav/<parent>/c/<category>/…` is shared across shops, so every
shop gets its own copy with a new timestamp — that is expected, not a bug.

Standing scope from the board owner, 23 Sep 2026: banners are wanted on
**TH, SG, Global, AU, HK** and deliberately **not** on MY, ID, Cambodia, PH.
TH has no banner for *Style in the City* (8967), so nothing can be copied for
that item anywhere — leave it.

## Cache: wait before reaching for a flush

Changes take roughly 10–20 minutes to appear, and self-resolve. Before
concluding anything is stuck:

- CloudFront returns `x-cache: Miss` on these pages — the CDN is not the
  cause and a purge achieves nothing.
- "Schedule Category Clean Cache" queues per *product category* and has not
  processed anything since 2019. Adding a row does nothing.
- Adv Settings → Performance "Clear Smarty cache" is **global across all
  nine shops plus marketplaces**, and this storefront is a separate app that
  server-renders the nav, so it is probably the wrong layer. Never flush it
  unilaterally during business hours.

Compare a shop that has already propagated against one that has not — that
distinguishes a real problem from a TTL that has not lapsed.

## Adding to this file

When something in a session would cost another session the same hour to
rediscover, add a short bullet here. Date an entry only when the date is
part of the lesson (a standing instruction from the board owner, or a
finding that could have changed since).
