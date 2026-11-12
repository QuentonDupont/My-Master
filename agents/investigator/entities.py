"""Ids a ticket mentions: returns, orders, POs, products, SKUs, transfer orders.

Requesters write ids every which way ("RMA139 4747", "PO 35505", a Henry URL, a bare
number next to the word "order"), so each kind has a few patterns and the results are
de-duplicated in the order they appear. A bare number is only taken when a keyword
sits right before it — guessing would point the database checks at the wrong record.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

_RMA = re.compile(r"\bRMA\s*#?\s*(\d[\d ]{4,10}\d)", re.I)
_ORDER = re.compile(r"\b(?:order|id_order|sale order|SO)\s*(?:id|no\.?|number)?\s*[#:]?\s*(\d{6,8})\b", re.I)
_APOLLO_ORDER_URL = re.compile(r"id_order=(\d{6,8})")
_PO = re.compile(r"\b(?:PO|purchase order)\s*(?:no\.?|number|#)?\s*[:#]?\s*(\d{4,6})\b", re.I)
_HENRY_ORDER_URL = re.compile(r"henry\.pomelofashion\.com/order/(\d{4,6})")
_HENRY_PRODUCT_URL = re.compile(r"henry\.pomelofashion\.com/product/(?:measurement/)?(\d{5,7})")
_PRODUCT = re.compile(r"\b(?:product|style|item|sku id|henry id|apollo id)\s*(?:id|no\.?)?\s*[#:]?\s*(\d{5,7})\b", re.I)
# "Revert four style 509690,509691,509744,509751 …" (PESD1-11397): every id in the list, not just the first
_PRODUCT_LIST = re.compile(r"\b(?:products?|styles?|items?)\b[^\d\n]{0,20}(\d{6}(?:\s*(?:,|/|&|and)\s*\d{6})+)", re.I)
_SKU_REF = re.compile(r"\b(\d{6}-\d{5,6})\b")            # product-variant reference, e.g. 151164-381846
_PO_INVOICE = re.compile(r"\b(\d{5})-(\d{5})\b")           # Henry PO-invoice pair, e.g. 33920-55620 (PESD1-11358)
_TRANSFER = re.compile(r"\b(TO-\d{6,10})\b", re.I)
_MC = re.compile(r"\bMC\s*[#:]?\s*(\d{3,6})\b")
_LINK = re.compile(r"https://(?:docs|drive)\.google\.com/[^\s|\]\)\"'<>]+")
_CUR = r"(RMB|CNY|USD|THB|SGD|MYR|HKD|PHP|IDR|AUD|EUR)"
_CURRENCY = re.compile(rf"\b{_CUR}\b")
_CURRENCY_TARGET = re.compile(rf"\bto\s+(?:be\s+)?{_CUR}\b", re.I)   # "from THB to RMB" → the ask is RMB


def _currencies(text: str) -> list[str]:
    norm = lambda c: "RMB" if c.upper() == "CNY" else c.upper()
    target = [norm(c) for c in _CURRENCY_TARGET.findall(text)]
    return _uniq(target) if target else _uniq(norm(c) for c in _CURRENCY.findall(text))


def _uniq(values) -> list:
    out = []
    for v in values:
        if v not in out:
            out.append(v)
    return out


@dataclass
class Entities:
    returns: list[int] = field(default_factory=list)
    orders: list[int] = field(default_factory=list)
    pos: list[int] = field(default_factory=list)
    invoices: list[int] = field(default_factory=list)   # Henry invoice ids (from PO-invoice pairs)
    products: list[int] = field(default_factory=list)
    sku_refs: list[str] = field(default_factory=list)
    transfers: list[str] = field(default_factory=list)
    collections: list[int] = field(default_factory=list)
    links: list[str] = field(default_factory=list)   # Google Sheets / Drive files that may hold the ids
    currencies: list[str] = field(default_factory=list)  # currencies the requester names (RMB, USD…)

    def merge(self, extra: dict) -> "Entities":
        """Ids a person supplies (e.g. read from the linked sheet) on top of the ones found."""
        for key, values in (extra or {}).items():
            if hasattr(self, key) and isinstance(values, list):
                setattr(self, key, _uniq(getattr(self, key) + values))
        return self

    def as_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items() if v}

    def empty(self) -> bool:
        return not self.as_dict()


def extract(text: str) -> Entities:
    """Every id the ticket text names, de-duplicated, in reading order."""
    text = text or ""
    return Entities(
        returns=_uniq(int(m.replace(" ", "")) for m in _RMA.findall(text)),
        orders=_uniq(int(m) for m in _ORDER.findall(text) + _APOLLO_ORDER_URL.findall(text)),
        pos=_uniq(int(m) for m in _PO.findall(text) + _HENRY_ORDER_URL.findall(text)
                  + [po for po, _ in _PO_INVOICE.findall(text)]),
        invoices=_uniq(int(inv) for _, inv in _PO_INVOICE.findall(text)),
        products=_uniq(int(m) for m in _HENRY_PRODUCT_URL.findall(text) + _PRODUCT.findall(text)
                       + [i for grp in _PRODUCT_LIST.findall(text) for i in re.findall(r"\d{6}", grp)]),
        sku_refs=_uniq(_SKU_REF.findall(text)),
        transfers=_uniq(m.upper() for m in _TRANSFER.findall(text)),
        collections=_uniq(int(m) for m in _MC.findall(text)),
        links=_uniq(_LINK.findall(text)),
        currencies=_currencies(text),
    )
