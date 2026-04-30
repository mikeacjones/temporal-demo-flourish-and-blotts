from shared.models import BookItem
from typing import Optional

CATALOG: list[BookItem] = [
    BookItem(
        id="mnbm-001",
        title="The Monster Book of Monsters",
        author="Edwardus Lima",
        price_galleons=5.3,
        description="Required for Care of Magical Creatures (Year 3). Bite-resistant gloves recommended. Stroke the spine to open safely.",
        category="dangerous",
        in_stock=12,
        cover_color="#8b4513",
    ),
    BookItem(
        id="hom-001",
        title="A History of Magic",
        author="Bathilda Bagshot",
        price_galleons=2.5,
        description="Comprehensive history of the wizarding world from the Middle Ages. Standard first-year Hogwarts textbook.",
        category="standard",
        in_stock=47,
        cover_color="#2d4a2d",
    ),
    BookItem(
        id="mpp-001",
        title="Moste Potente Potions",
        author="Phineas Bourne",
        price_galleons=12.0,
        description="Advanced potion recipes including Polyjuice Potion. Restricted Section. Ministry approval and signed instructor permission required.",
        category="restricted",
        in_stock=3,
        requires_ministry_approval=True,
        age_restriction=17,
        cover_color="#4a1942",
    ),
    BookItem(
        id="bs-001",
        title="Break with a Banshee",
        author="Gilderoy Lockhart",
        price_galleons=1.4,
        description="One of Gilderoy Lockhart's many thrilling adventures. Authenticity not guaranteed. Winner of Witch Weekly's Most-Charming-Smile Award.",
        category="standard",
        # OMS believes there are plenty in stock — customers can order it freely.
        in_stock=156,
        # …but the warehouse shelf is actually empty. pick_and_pack will discover
        # this at fulfilment time and raise INVENTORY_MISMATCH, kicking the order
        # into the substitution-HITL flow (Voyages with Vampires, same author, is
        # the obvious candidate).
        physical_in_stock=0,
        cover_color="#c9a84c",
    ),
    BookItem(
        id="voyages-001",
        title="Voyages with Vampires",
        author="Gilderoy Lockhart",
        price_galleons=1.4,
        description="Lockhart's vampire-hunting exploits, as only he could tell them. Part of the Hogwarts Year 2 curriculum.",
        category="standard",
        in_stock=134,
        cover_color="#c9a84c",
    ),
    BookItem(
        id="tdda-001",
        title="The Dark Arts Outsmarted",
        author="Wilbert Slinkhard",
        price_galleons=8.0,
        description="Defensive strategies against Dark Magic. Ministry of Magic approved curriculum text. Umbridge-endorsed.",
        category="standard",
        in_stock=23,
        cover_color="#1a3a5c",
    ),
    BookItem(
        id="bosl-001",
        title="Book of Spells",
        author="Miranda Goshawk",
        price_galleons=3.7,
        description="Standard Book of Spells, Grade 1. The essential companion for first-year students at Hogwarts.",
        category="standard",
        in_stock=89,
        cover_color="#2d6a2d",
    ),
    BookItem(
        id="fbwtft-001",
        title="Fantastic Beasts and Where to Find Them",
        author="Newt Scamander",
        price_galleons=4.2,
        description="The definitive guide to magical creatures by Magizoologist Newt Scamander. Approved by the Ministry. Now in its 52nd edition.",
        category="standard",
        in_stock=31,
        cover_color="#5c3a1a",
    ),
    BookItem(
        id="drk-001",
        title="Secrets of the Darkest Art",
        author="Unknown",
        price_galleons=20.0,
        description="Contains instructions for creating Horcruxes and other Dark Magic of the highest order. SEVERELY restricted. Ministry approval, academic credentials, and in-person identity verification required.",
        category="restricted",
        in_stock=1,
        requires_ministry_approval=True,
        age_restriction=18,
        cover_color="#3a0000",
    ),
    BookItem(
        id="qta-001",
        title="Quidditch Through the Ages",
        author="Kennilworthy Whisp",
        price_galleons=2.0,
        description="The complete history of the world's most popular wizarding sport, from the Queerditch Marsh origins to the Chudley Cannons.",
        category="standard",
        in_stock=67,
        cover_color="#8b0000",
    ),
]


def get_book_by_id(book_id: str) -> Optional[BookItem]:
    return next((b for b in CATALOG if b.id == book_id), None)
