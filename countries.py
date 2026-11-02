"""Decide whether a job's location text lets someone in a given country apply.

Location strings look like "Remote, United States", "Remote, EMEA, LATAM, Canada, USA" or "Remote, worldwide".
A job counts as open to a country when the text names that country, says worldwide, or names a region that
contains it (EMEA, Africa, MENA...). Anything else is treated as not open, and bare "Remote" as unclear.
"""
import re

EUROPE = {"albania", "andorra", "austria", "belgium", "bosnia and herzegovina", "bulgaria", "croatia", "cyprus",
          "czechia", "denmark", "estonia", "finland", "france", "germany", "greece", "hungary", "iceland", "ireland",
          "italy", "kosovo", "latvia", "lithuania", "luxembourg", "malta", "moldova", "montenegro", "netherlands",
          "north macedonia", "norway", "poland", "portugal", "romania", "serbia", "slovakia", "slovenia", "spain",
          "sweden", "switzerland", "ukraine", "united kingdom", "georgia", "armenia", "turkey"}
EU = EUROPE - {"albania", "andorra", "bosnia and herzegovina", "iceland", "kosovo", "moldova", "montenegro",
               "north macedonia", "norway", "serbia", "switzerland", "ukraine", "united kingdom", "georgia",
               "armenia", "turkey"}
NORTH_AFRICA = {"morocco", "algeria", "tunisia", "libya", "egypt"}
AFRICA = NORTH_AFRICA | {"nigeria", "kenya", "ghana", "south africa", "ethiopia", "rwanda", "uganda", "tanzania",
                         "senegal", "ivory coast", "cameroon", "zambia", "zimbabwe", "botswana", "namibia",
                         "mauritius", "madagascar", "mozambique", "angola", "benin", "togo", "mali"}
MIDDLE_EAST = {"united arab emirates", "saudi arabia", "qatar", "kuwait", "bahrain", "oman", "jordan", "lebanon",
               "israel", "iraq", "iran", "turkey", "palestine", "yemen", "syria"}
NORTH_AMERICA = {"united states", "canada", "mexico"}
LATAM = {"mexico", "brazil", "argentina", "colombia", "chile", "peru", "uruguay", "paraguay", "bolivia", "ecuador",
         "venezuela", "costa rica", "panama", "guatemala", "honduras", "el salvador", "nicaragua",
         "dominican republic", "cuba", "puerto rico"}
APAC = {"india", "pakistan", "bangladesh", "sri lanka", "nepal", "china", "hong kong", "taiwan", "japan",
        "south korea", "singapore", "malaysia", "indonesia", "philippines", "vietnam", "thailand", "australia",
        "new zealand", "cambodia"}

# region words that can appear in a location string -> the countries they cover
REGION_WORDS = {
    "emea": EUROPE | AFRICA | MIDDLE_EAST, "europe": EUROPE, "eu": EU, "european union": EU,
    "africa": AFRICA, "north africa": NORTH_AFRICA, "mena": MIDDLE_EAST | NORTH_AFRICA,
    "middle east": MIDDLE_EAST, "gcc": {"united arab emirates", "saudi arabia", "qatar", "kuwait", "bahrain", "oman"},
    "americas": NORTH_AMERICA | LATAM, "north america": NORTH_AMERICA, "latam": LATAM, "latin america": LATAM,
    "south america": LATAM, "apac": APAC, "asia": APAC, "asia pacific": APAC,
}
WORLDWIDE = re.compile(r"\b(worldwide|anywhere|global|globally|any location|international)\b", re.I)
ALIASES = {"united states": ["usa", "us", "u.s.", "united states of america"],
           "united kingdom": ["uk", "england", "scotland", "wales", "great britain", "britain"],
           "united arab emirates": ["uae", "dubai", "abu dhabi"], "czechia": ["czech republic"],
           "south korea": ["korea"], "netherlands": ["the netherlands", "holland"], "turkey": ["turkiye", "türkiye"],
           "ivory coast": ["cote d'ivoire", "côte d'ivoire"], "morocco": ["maroc"]}
ALL = EUROPE | AFRICA | MIDDLE_EAST | NORTH_AMERICA | LATAM | APAC


def canonical(country):
    c = re.sub(r"\s+", " ", country.lower().strip())
    for name, alts in ALIASES.items():
        if c == name or c in alts:
            return name
    return c


def _names(country):
    return [country] + ALIASES.get(country, [])


def match(location, country):
    """Return why `location` is open to `country` ('lists Morocco', 'worldwide', 'EMEA includes Morocco'),
    'unclear' when the text gives no country or region, or None when it is limited elsewhere."""
    text = (location or "").lower()
    if any(re.search(rf"(?<![a-z]){re.escape(n)}(?![a-z])", text) for n in _names(country)):
        return f"lists {country.title()}"
    # blank out country names first, so "South Africa" is not read as the region "Africa"
    rest = text
    for name in sorted(ALL | {a for alts in ALIASES.values() for a in alts}, key=len, reverse=True):
        rest = re.sub(rf"(?<![a-z]){re.escape(name)}(?![a-z])", " ", rest)
    if WORLDWIDE.search(rest):
        return "worldwide"
    for word, members in sorted(REGION_WORDS.items(), key=lambda kv: -len(kv[0])):
        if country in members and re.search(rf"(?<![a-z]){re.escape(word)}(?![a-z])", rest):
            return f"{word.upper() if len(word) <= 4 else word.title()} includes {country.title()}"
    leftover = re.sub(r"\b(remote|hybrid|see listing|\(see post\))\b|[,;|()/-]", " ", text).strip()
    return "unclear" if not leftover else None
