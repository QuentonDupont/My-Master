"""The Apollo content surfaces this team is allowed to touch, and how each one
stages a change.

Quenton's standing instruction, 21 Sep 2026: *"all changes need to inactive, if
not possible then please continue with the change"*. So every surface here
declares how a change can be parked out of customer view until he activates it.
`staging` is the contract:

    INACTIVE   the surface has a visibility/active flag — set it off, save, done.
               The safe case. Nothing reaches a customer until Quenton flips it.
    SCHEDULED  no on/off flag, but it takes a date window — set the window to
               start in the future so it cannot render today.
    MARKET     no flag and no window, but content is per-market — stage it with
               every market unticked, which renders it nowhere.
    LIVE       none of the above exist. The change goes in hot. Per the standing
               instruction we still proceed, but the proposal is flagged
               `stages_live` and the report says so in as many words, because
               that is the case Quenton needs to see before it happens, not
               after.

Nothing is inferred at runtime. A surface that is not in this table is not
something this team touches — `resolve()` raises rather than guessing, because
guessing which control makes a banner invisible is how you publish one.
"""
from __future__ import annotations

from dataclasses import dataclass, field

INACTIVE = "INACTIVE"
SCHEDULED = "SCHEDULED"
MARKET = "MARKET"
LIVE = "LIVE"
STAGING = (INACTIVE, SCHEDULED, MARKET, LIVE)

WEB = "web"
APP = "app"
BOTH = "both"


class UnknownSurface(KeyError):
    """Raised for any surface not declared below."""


@dataclass(frozen=True)
class Surface:
    key: str
    label: str
    platform: str
    #: Where a human finds it in Apollo, written the way the menu reads.
    path: str
    staging: str
    #: The control that parks the change, named as it appears on screen.
    staging_control: str
    #: Confluence refs, highest-authority first. The worker must read these
    #: before drafting; they are the procedure, this file is only the index.
    sops: tuple[str, ...] = ()
    notes: str = ""

    @property
    def stages_live(self) -> bool:
        return self.staging == LIVE


SURFACES: dict[str, Surface] = {
    "web_hero": Surface(
        key="web_hero",
        label="Homepage hero slider (web)",
        platform=WEB,
        path="Pomelo Website > Homepage Manager V3 > Slider",
        staging=MARKET,
        staging_control="the per-field market checkboxes "
                        "(EN/TH/SG/US/ID/AU/MY/ZH/HK/PH/KH)",
        sops=("PM/Add / edit banner: use case",
              "PM/Banners: User id required feature"),
        notes="Content is keyed by market checkbox rather than by shop row, so "
              "'disable for HK' means unticking HK, not deleting the slide. "
              "Verified in the UI on 21 Sep 2026.",
    ),
    "web_menu_new_in": Surface(
        key="web_menu_new_in",
        label="Menu: New In (web)",
        platform=WEB,
        path="Pomelo Website > Homepage Manager V3 > Menu New In",
        staging=MARKET,
        staging_control="the per-field market checkboxes",
        sops=("PM/How to: Category Module",),
    ),
    "web_menu_featured": Surface(
        key="web_menu_featured",
        label="Menu: Featured (web)",
        platform=WEB,
        path="Pomelo Website > Homepage Manager V3 > Menu Featured",
        staging=INACTIVE,
        staging_control="Visibility = No",
        sops=("PM/How to: Category Module",),
        notes="Has an explicit Visibility Yes/No radio — the cleanest surface "
              "on the web side. Prefer it when a task could go either here or "
              "into a slider.",
    ),
    "web_masonry": Surface(
        key="web_masonry",
        label="Masonry grid (web)",
        platform=WEB,
        path="Pomelo Website > Homepage Manager V3 > Masonry",
        staging=MARKET,
        staging_control="the per-field market checkboxes",
        sops=(),
    ),
    "web_video": Surface(
        key="web_video",
        label="Homepage video (web)",
        platform=WEB,
        path="Pomelo Website > Homepage Manager V3 > Video",
        staging=MARKET,
        staging_control="the per-field market checkboxes",
        sops=(),
    ),
    "skinny_banner": Surface(
        key="skinny_banner",
        label="Skinny banner strip",
        platform=BOTH,
        path="Pomelo Website > Skinny Banners",
        staging=SCHEDULED,
        staging_control="the valid-from / valid-to date window",
        sops=("PM/How to: Skinny Banner Module",),
        notes="The one surface still written daily in the database "
              "(pml_skinny_banner, last write 15 Sep 2026), so treat it as the "
              "live one and the homepage_slider table as dead.",
    ),
    "category_module": Surface(
        key="category_module",
        label="Category module / category banners",
        platform=BOTH,
        path="Catalog > Categories (Category Module)",
        staging=SCHEDULED,
        staging_control="the banner scheduling window",
        sops=("PM/How to: Category Module",
              "PM/Category Module: Banner scheduling feature",
              "PM/Apollo Categories Module: Checklist",
              "PM/Apollo How To: Add T&C Pop Up for Category Banner to Category Module",
              "PM/Apollo How To: Category Dashboard"),
    ),
    "app_hero": Surface(
        key="app_hero",
        label="App hero feed",
        platform=APP,
        path="Native App > Feeds V3 > Hero",
        staging=MARKET,
        staging_control="the market/language selector on the feed",
        sops=("PM/Apollo How To: App Feed Card Module",),
        notes="Feeds V3 sections are Hero, Featured, Feed, Brand Awareness, "
              "Social Looks. Confirmed in the UI on 21 Sep 2026.",
    ),
    "app_featured": Surface(
        key="app_featured", label="App featured feed", platform=APP,
        path="Native App > Feeds V3 > Featured", staging=MARKET,
        staging_control="the market/language selector on the feed",
        sops=("PM/Apollo How To: App Feed Card Module",),
    ),
    "app_feed": Surface(
        key="app_feed", label="App main feed", platform=APP,
        path="Native App > Feeds V3 > Feed", staging=MARKET,
        staging_control="the market/language selector on the feed",
        sops=("PM/Apollo How To: App Feed Card Module",),
    ),
    "app_brand_awareness": Surface(
        key="app_brand_awareness", label="App brand awareness feed",
        platform=APP, path="Native App > Feeds V3 > Brand Awareness",
        staging=MARKET, staging_control="the market/language selector",
        sops=("PM/Apollo How To: App Feed Card Module",),
    ),
    "app_social_looks": Surface(
        key="app_social_looks", label="App social looks feed", platform=APP,
        path="Native App > Feeds V3 > Social Looks", staging=MARKET,
        staging_control="the market/language selector",
        sops=("PM/Apollo How To: App Feed Card Module",),
    ),
    "lookbook": Surface(
        key="lookbook", label="Lookbook", platform=WEB,
        path="Pomelo Website > Lookbook Manager", staging=INACTIVE,
        staging_control="the Status toggle in the Lookbook Manager list "
                        "(green tick = active; set it inactive before saving)",
        sops=(),
        notes="VERIFIED in the UI 21 Sep 2026. This was first written down as "
              "LIVE on save, which was wrong — the list has a Status column "
              "with an active/inactive toggle per row, alongside Preview, Edit "
              "and Delete. Lookbook is one of the safer surfaces, not one of "
              "the dangerous ones. Shiraz's 'Exclusive Partner Deals' content "
              "lives here: row id 277, 'Partner Deals - August/26', category "
              "8968.",
    ),
    "holiday_announcement": Surface(
        key="holiday_announcement", label="Holiday announcement", platform=WEB,
        path="Pomelo Website > Holiday Announcement", staging=SCHEDULED,
        staging_control="the announcement date window", sops=(),
    ),
    "instashop": Surface(
        key="instashop", label="Instashop", platform=WEB,
        path="Pomelo Website > Instashop", staging=LIVE, staging_control="",
        sops=(),
        notes="UNVERIFIED, not proven dangerous. Opened on 21 Sep 2026: it was "
              "empty for TH, with an Add Content button and a per-language "
              "switcher, and confirming whether a visibility flag exists would "
              "have meant adding content to production. LIVE is the fail-safe "
              "classification, so it stays here until someone checks — it means "
              "'we cannot promise this parks', not 'we know it publishes'. "
              "Reclassify once it has real content to look at.",
    ),
}

#: Markets, as the checkboxes are labelled in Homepage Manager V3.
MARKETS = ("EN", "TH", "SG", "US", "ID", "AU", "MY", "ZH", "HK", "PH", "KH")

#: The shops behind those markets, as the multistore selector lists them.
SHOPS = ("Pomelo", "Pomelo AU", "Pomelo Global", "Pomelo HK", "Pomelo ID",
         "Pomelo KH (Cambodia)", "Pomelo MY", "Pomelo PH", "Pomelo SG")


def resolve(key: str) -> Surface:
    """The surface, or a refusal. Never a guess."""
    try:
        return SURFACES[key]
    except KeyError:
        raise UnknownSurface(
            f"{key!r} is not a declared Apollo surface. Known: "
            f"{', '.join(sorted(SURFACES))}. Add it to surfaces.py with its "
            f"staging control before asking a worker to touch it."
        ) from None


def for_platform(platform: str) -> list[Surface]:
    return [s for s in SURFACES.values()
            if s.platform == platform or s.platform == BOTH]


def live_staging_surfaces() -> list[Surface]:
    """Surfaces where a change cannot be parked. Quenton asked to proceed on
    these anyway — this list is what the lead warns him about up front."""
    return [s for s in SURFACES.values() if s.stages_live]
