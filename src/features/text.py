"""Признаки из текста описания: ремонт, мебель, техника, условия.

Простые словари + regex. Описание пишет либо агент (шаблонно), либо
собственник (вольно) — словари покрывают оба стиля. Все признаки бинарные,
отсутствие описания = нули (информация «нет описания» уже в has_description).
"""

from __future__ import annotations

import re

import pandas as pd

# паттерн -> имя признака
PATTERNS: dict[str, str] = {
    # ремонт (иерархия: евро > хороший > косметический > требует)
    r"евроремонт|дизайнерск\w+ ремонт|ремонт[\s:]*евро": "renov_euro",
    r"косметическ\w+ ремонт": "renov_cosmetic",
    r"(требует\w*|без) ремонт\w*|под ремонт": "renov_needed",
    # обстановка
    # «мебель» упоминает половина объявлений в самых вольных формулировках
    # («вся необходимая мебель», «укомплектована мебелью») — ловим широко,
    # явное «без мебели» перекрывает (см. extract_text_features)
    r"мебел|меблирован": "furnished",
    r"без мебели|мебели нет|не меблирован": "unfurnished",
    r"посудомо\w+": "dishwasher",
    r"стиральн\w+ машин\w+": "washer",
    r"кондиционер": "aircon",
    r"холодильник": "fridge",
    # условия
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
