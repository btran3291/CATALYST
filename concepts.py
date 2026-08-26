"""Canonical concept -> synonym XBRL tag mapping, across taxonomies.

The same real-world figure gets reported under different tags across time
(ASC 606 adoption, tax-inclusive vs -exclusive variants, older SFAS-era
tags) and a single company can switch tags between filings, as AST
SpaceMobile did for FY2023 revenue. It also gets reported under an entirely
different taxonomy for foreign private issuers filing 20-F under IFRS
(ifrs-full) instead of 10-K under US GAAP (us-gaap) — SatixFy Communications
files IFRS-only and has zero us-gaap facts at all. Ingestion and stage
classification need every synonym covered, across taxonomies, never just
whichever tag happens to return data first.
"""

CONCEPT_ALIASES = {
    "revenue": [
        ("us-gaap", "Revenues"),
        ("us-gaap", "RevenueFromContractWithCustomerExcludingAssessedTax"),
        ("us-gaap", "RevenueFromContractWithCustomerIncludingAssessedTax"),
        ("us-gaap", "SalesRevenueNet"),
        # Deliberately NOT including SalesRevenueGoodsNet or
        # SalesRevenueServicesNet — same mistake as the IFRS split tags
        # below, just caught later: these are components (goods + services
        # = total), not synonyms of the total. Confirmed against Sorrento
        # Therapeutics' actual data — SalesRevenueServicesNet ($8,375) is a
        # small subset of Revenues ($460,148) for the same period, not a
        # competing figure for the same total. Caused 126 false conflicts.
        ("ifrs-full", "Revenue"),
        ("ifrs-full", "RevenueFromContractsWithCustomers"),
        # Deliberately NOT including RevenueFromSaleOfGoods or
        # RevenueFromRenderingOfServices — those are components (goods +
        # services = total), not synonyms of the total. Confirmed against
        # SatixFy's actual data: 19,237,000 + 2,483,000 = 21,720,000 =
        # Revenue. Including them caused 66 false alias conflicts.
    ],
}


def alias_specs(canonical: str) -> list[tuple[str, str]]:
    """(taxonomy, tag) pairs for a canonical concept — what ingestion
    iterates to know which taxonomy dict to look each tag up in."""
    return CONCEPT_ALIASES[canonical]


def tags_for(canonical: str) -> list[str]:
    """Fully-qualified 'taxonomy:tag' identifiers, in priority order — what
    gets stored in facts.concept and what consumers (e.g. quarters.py) use
    to query/rank alias tags. Qualified so a us-gaap and an ifrs-full tag
    that happen to share a bare name can never collide or get blurred."""
    return [f"{taxonomy}:{tag}" for taxonomy, tag in CONCEPT_ALIASES[canonical]]
