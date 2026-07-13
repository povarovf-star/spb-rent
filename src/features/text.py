"""Features from the description text: renovation, furniture, appliances, terms.

Simple dictionaries + regex. The description is written either by an agent
(templated) or an owner (free-form), and the dictionaries cover both styles. All
features are binary; a missing description gives zeros (the "no description" signal
is already in has_description).
"""

from __future__ import annotations

import re

import pandas as pd

# pattern -> feature name
PATTERNS: dict[str, str] = {
    # renovation (hierarchy: euro > good > cosmetic > needed)
    r"евроремонт|дизайнерск\w+ ремонт|ремонт[\s:]*евро": "renov_euro",
    r"косметическ\w+ ремонт": "renov_cosmetic",
    r"(требует\w*|без) ремонт\w*|под ремонт": "renov_needed",
    # furnishing
    # half of the listings mention "furniture" in the loosest wording
    # ("all the furniture you need", "furnished"), so match broadly;
    # an explicit "no furniture" overrides it (see extract_text_features)
    r"мебел|меблирован": "furnished",
    r"без мебели|мебели нет|не меблирован": "unfurnished",
    r"посудомо\w+": "dishwasher",
    r"стиральн\w+ машин\w+": "washer",
    r"кондиционер": "aircon",
    r"холодильник": "fridge",
    # terms
    r"можно с животными|с питомц\w+|животны\w+ (можно|разрешены)|pet-?friendly": "pets_ok",
    r"без животных|животны\w+ (нельзя|запрещ\w+)": "pets_no",
    r"без детей": "kids_no",
    r"балкон|лоджи\w+": "balcony",
    r"вид на (воду|залив|неву|канал|реку|парк)": "nice_view",
    r"паркинг|парковк\w+|машиноместо": "parking",
    r"консьерж": "concierge",
    r"новостройк\w+|новый дом|дом \d{4} года постройки": "new_building",
}

_COMPILED = {re.compile(p, re.IGNORECASE): name for p, name in PATTERNS.items()}


def extract_text_features(desc: str | None) -> dict[str, int]:
    feats = dict.fromkeys(_COMPILED.values(), 0)
    if not desc:
        feats["has_description"] = 0
        feats["desc_len"] = 0
        return feats
    for rx, name in _COMPILED.items():
        if rx.search(desc):
            feats[name] = 1
    if feats["unfurnished"]:
        feats["furnished"] = 0
    feats["has_description"] = 1
    feats["desc_len"] = len(desc)
    return feats


def add_text_features(df: pd.DataFrame) -> pd.DataFrame:
    feats = pd.DataFrame(
        [extract_text_features(d) for d in df["description"]], index=df.index
    )
    return pd.concat([df, feats], axis=1)
