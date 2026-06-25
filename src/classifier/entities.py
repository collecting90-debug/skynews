"""
src/classifier/entities.py
───────────────────────────
Curated entity knowledge base for UAE, Arab, and Global classification.

This file is the single source of truth for entity lists.
Extend each set to improve classification accuracy.
"""

from __future__ import annotations

# ─────────────────────────────────────────────────────────────────────────────
# UAE ENTITIES
# ─────────────────────────────────────────────────────────────────────────────

UAE_COUNTRIES = {
    "uae", "united arab emirates", "emirates", "emirati",
}

UAE_CITIES = {
    "dubai", "abu dhabi", "sharjah", "ajman", "ras al khaimah",
    "fujairah", "umm al quwain", "al ain", "kalba", "khor fakkan",
}

UAE_FOOTBALL_CLUBS = {
    # UAE Pro League
    "al ain", "al-ain", "al ain fc",
    "shabab al ahli", "al ahli dubai",
    "al wasl", "al-wasl",
    "al jazira", "al-jazira",
    "sharjah fc", "sharjah",
    "al nasr", "al-nasr dubai",
    "baniyas", "baniyas sc",
    "ittihad kalba",
    "al dhafra",
    "dibba al fujairah",
    "khorfakkan",
    "ajman",
    "emirates club",
    "al hamriyah",
    "masfout",
    "al orooba",
}

UAE_SPORTS_BODIES = {
    "uae football association", "uaefa", "uae olympic committee",
    "uae national team", "uae u23", "uae u20",
    "abu dhabi sports council", "dubai sports council",
    "uae camel racing federation", "uae equestrian federation",
    "uae archery federation", "uae cycling federation",
    "uae judo federation", "uae wrestling federation",
    "uae athletics federation", "uae swimming federation",
    "uae tennis federation", "uae basketball federation",
    "abu dhabi grand prix", "uae tour", "uae triathlon",
    "west asia archery federation",
    "west asia cup",
}

UAE_PLAYERS_AND_PERSONALITIES = {
    # Notable UAE sportspeople — extend as needed
    "ali mabkhout", "khalil ibrahim", "caio canedo",
    "sebastian taggart", "mahdi ali",
    "hamdan bin mohammed", "mansoor bin zayed",
    "theyab awana",
}

UAE_VENUES = {
    "al maktoum stadium", "al nahyan stadium", "hazza bin zayed stadium",
    "khalifa international stadium", "al jazira mohammed bin zayed stadium",
    "yas marina circuit", "dubai autodrome", "meydan racecourse",
    "al ain equestrian", "fujairah stadium",
}

# Aggregate all UAE signals into one set (lower-cased)
UAE_ENTITIES: set[str] = (
    UAE_COUNTRIES
    | UAE_CITIES
    | UAE_FOOTBALL_CLUBS
    | UAE_SPORTS_BODIES
    | UAE_PLAYERS_AND_PERSONALITIES
    | UAE_VENUES
)


# ─────────────────────────────────────────────────────────────────────────────
# ARAB ENTITIES
# ─────────────────────────────────────────────────────────────────────────────

ARAB_COUNTRIES = {
    "saudi arabia", "saudi", "ksa",
    "egypt", "egyptian",
    "jordan", "jordanian",
    "morocco", "moroccan",
    "algeria", "algerian",
    "tunisia", "tunisian",
    "libya", "libyan",
    "iraq", "iraqi",
    "syria", "syrian",
    "lebanon", "lebanese",
    "palestine", "palestinian",
    "qatar", "qatari",
    "kuwait", "kuwaiti",
    "bahrain", "bahraini",
    "oman", "omani",
    "yemen", "yemeni",
    "sudan", "sudanese",
    "mauritania", "mauritanian",
    "comoros", "comorian",
    "djibouti", "djiboutian",
    "somalia", "somali",
    "arab world", "arab", "arabic", "gcc",
}

ARAB_FOOTBALL_CLUBS = {
    # Saudi
    "al hilal", "al nassr", "al ittihad", "al ahli saudi",
    "al qadsiah", "al shabab", "al taawoun", "al fayha",
    # Egypt
    "al ahly", "zamalek", "pyramids fc",
    # Qatar
    "al sadd", "al rayyan", "al duhail",
    # Morocco
    "wydad", "raja casablanca",
    # Jordan
    "al faisaly", "al wehdat",
    # Iraq
    "al zawraa", "al shorta",
    # Generic
    "arab cup", "arab nations cup", "pan arab games",
    "gulf cup", "gcc championship",
    "afcon", "africa cup of nations",
    "waff championship",
    "asian football confederation",  # includes Arab member states context
}

ARAB_SPORTS_BODIES = {
    "arab sports federation", "arab olympic committee",
    "arab shooting federation", "arab equestrian federation",
    "arab athletics federation", "arab swimming federation",
    "arab basketball federation", "arab football federations union",
    "pan arab games committee", "gulf cooperation council",
}

ARAB_ENTITIES: set[str] = (
    ARAB_COUNTRIES
    | ARAB_FOOTBALL_CLUBS
    | ARAB_SPORTS_BODIES
)


# ─────────────────────────────────────────────────────────────────────────────
# GLOBAL / INTERNATIONAL ENTITIES
# ─────────────────────────────────────────────────────────────────────────────

GLOBAL_SPORTS_BODIES = {
    "fifa", "uefa", "afc", "caf", "concacaf", "conmebol", "ofc",
    "ioc", "wada", "world athletics", "fis", "fia", "fide",
    "world cup", "olympic games", "olympics", "paralympics",
    "champions league", "europa league", "world championship",
    "grand slam", "wimbledon", "us open", "french open", "australian open",
    "formula 1", "formula one", "f1",
}

GLOBAL_COUNTRIES = {
    "usa", "united states", "america", "american",
    "england", "uk", "britain", "british",
    "france", "french", "germany", "german",
    "spain", "spanish", "italy", "italian",
    "portugal", "portuguese", "netherlands", "dutch",
    "brazil", "brazilian", "argentina", "argentinian",
    "china", "chinese", "japan", "japanese",
    "south korea", "korean", "australia", "australian",
    "russia", "russian", "ukraine", "ukrainian",
    "india", "indian", "pakistan", "pakistani",
    "nigeria", "senegal", "ivory coast",
    # etc — not exhaustive; NER handles the rest
}

GLOBAL_FOOTBALL_CLUBS = {
    "real madrid", "barcelona", "manchester united", "manchester city",
    "liverpool", "chelsea", "arsenal", "tottenham",
    "juventus", "inter milan", "ac milan", "napoli",
    "paris saint-germain", "psg", "lyon", "marseille",
    "bayern munich", "borussia dortmund",
    "atletico madrid", "sevilla",
    "ajax", "porto", "benfica",
}

GLOBAL_ENTITIES: set[str] = (
    GLOBAL_SPORTS_BODIES
    | GLOBAL_COUNTRIES
    | GLOBAL_FOOTBALL_CLUBS
)
