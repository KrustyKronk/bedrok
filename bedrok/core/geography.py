"""Places: which geography a series is about, and which words name one.

One table, used by search (a place in the question filters the catalog rather
than being scored like any other word), by the warnings search, and by the
test that every name leads with its geography.

Series ids carry their geography in one of two places: first, by convention
(`es_hicp_yoy`, `us_10y`), or last, for the families the bulk stages build
(`hicp_core_es`, `gdp_qoq_ea20`, `oecd_cli_deu`). Commodity and positioning
series have no geography of their own: "us" in `wb_banana_us` is where the
banana price is quoted, not a country to search by.

Pure data, in core: nothing here imports a layer above.
"""
from __future__ import annotations

import re

# id code → how names begin (the names test) and a readable label.
CODES = {
    "us": "US", "ea": "Euro area", "eu": "EU", "uk": "UK", "jp": "Japan",
    "cn": "China", "ca": "Canada", "au": "Australia", "ch": "Switzerland",
    "de": "Germany", "fr": "France", "it": "Italy", "es": "Spain",
    "nl": "Netherlands", "be": "Belgium", "at": "Austria", "pt": "Portugal",
    "ie": "Ireland", "el": "Greece", "fi": "Finland", "se": "Sweden",
    "no": "Norway", "dk": "Denmark", "kz": "Kazakhstan",
}

# Words a person types for a place → its code. Two-letter country codes that
# are also common words (it, no, de, es, at, be, ie) are deliberately absent:
# "it" in a question is a pronoun before it is Italy.
NAMES = {
    "us": "us", "usa": "us", "america": "us", "american": "us", "united states": "us",
    "uk": "uk", "britain": "uk", "british": "uk", "united kingdom": "uk", "england": "uk",
    "euro area": "ea", "eurozone": "ea", "euro-area": "ea", "ea": "ea",
    "eu": "eu", "european union": "eu",
    "japan": "jp", "japanese": "jp", "china": "cn", "chinese": "cn",
    "canada": "ca", "canadian": "ca", "australia": "au", "australian": "au",
    "switzerland": "ch", "swiss": "ch", "germany": "de", "german": "de",
    "france": "fr", "french": "fr", "italy": "it", "italian": "it",
    "spain": "es", "spanish": "es", "netherlands": "nl", "dutch": "nl",
    "belgium": "be", "belgian": "be", "austria": "at", "austrian": "at",
    "portugal": "pt", "portuguese": "pt", "ireland": "ie", "irish": "ie",
    "greece": "el", "greek": "el", "finland": "fi", "finnish": "fi",
    "sweden": "se", "swedish": "se", "norway": "no", "norwegian": "no",
    "denmark": "dk", "danish": "dk",
    "kazakhstan": "kz", "kazakh": "kz", "kazakhstani": "kz",
}

# Places Bedrok carries only as the last token of a broad family (Eurostat's
# HICP for every European country, `hicp_yoy_tr`). They are not in CODES: no
# seed id leads with them, and the names test pins CODES to how seed names
# begin. Without them a stored Turkish series could not be found by "turkey"
# (the 2026-10-12 self-test).
FAMILY_CODES = {
    "al": "Albania", "bg": "Bulgaria", "cy": "Cyprus", "cz": "Czechia", "ee": "Estonia",
    "ge": "Georgia", "hr": "Croatia", "hu": "Hungary", "is": "Iceland", "lt": "Lithuania",
    "lu": "Luxembourg", "lv": "Latvia", "me": "Montenegro", "mk": "North Macedonia",
    "mt": "Malta", "pl": "Poland", "ro": "Romania", "rs": "Serbia", "si": "Slovenia",
    "sk": "Slovakia", "tr": "Türkiye", "ba": "Bosnia and Herzegovina", "md": "Moldova",
    "ua": "Ukraine",
}

# Their names. Never the bare codes: "is" and "me" are words. Georgia is left
# out on purpose: it is a US state before it is a country in most questions.
NAMES.update({
    "albania": "al", "albanian": "al", "bulgaria": "bg", "bulgarian": "bg",
    "cyprus": "cy", "cypriot": "cy", "czechia": "cz", "czech": "cz",
    "estonia": "ee", "estonian": "ee", "croatia": "hr", "croatian": "hr",
    "hungary": "hu", "hungarian": "hu", "iceland": "is", "icelandic": "is",
    "lithuania": "lt", "lithuanian": "lt", "luxembourg": "lu", "latvia": "lv",
    "latvian": "lv", "montenegro": "me", "north macedonia": "mk", "macedonia": "mk",
    "malta": "mt", "maltese": "mt", "poland": "pl", "polish": "pl", "romania": "ro",
    "romanian": "ro", "serbia": "rs", "serbian": "rs", "slovenia": "si",
    "slovenian": "si", "slovakia": "sk", "slovak": "sk", "turkey": "tr",
    "turkish": "tr", "turkiye": "tr", "bosnia": "ba", "bosnian": "ba",
    "moldova": "md", "moldovan": "md",
    "ukraine": "ua", "ukrainian": "ua",
})

# Every other economy the IMF's World Economic Outlook covers (the world layer,
# 2026-11-19): code → name. Generated from ISO 3166 (pycountry), names tidied.
FAMILY_CODES.update({
    "ad": "Andorra", "ae": "United Arab Emirates", "af": "Afghanistan",
    "ag": "Antigua and Barbuda", "am": "Armenia", "ao": "Angola", "ar": "Argentina",
    "aw": "Aruba", "az": "Azerbaijan", "bb": "Barbados", "bd": "Bangladesh",
    "bf": "Burkina Faso", "bh": "Bahrain", "bi": "Burundi", "bj": "Benin", "bn": "Brunei",
    "bo": "Bolivia", "br": "Brazil", "bs": "Bahamas", "bt": "Bhutan", "bw": "Botswana",
    "by": "Belarus", "bz": "Belize", "cd": "DR Congo", "cf": "Central African Republic",
    "cg": "Republic of the Congo", "ci": "Côte d'Ivoire", "cl": "Chile", "cm": "Cameroon",
    "co": "Colombia", "cr": "Costa Rica", "cv": "Cabo Verde", "dj": "Djibouti",
    "dm": "Dominica", "do": "Dominican Republic", "dz": "Algeria", "ec": "Ecuador",
    "eg": "Egypt", "er": "Eritrea", "et": "Ethiopia", "fj": "Fiji", "fm": "Micronesia",
    "ga": "Gabon", "gd": "Grenada", "gh": "Ghana", "gm": "Gambia", "gn": "Guinea",
    "gq": "Equatorial Guinea", "gt": "Guatemala", "gw": "Guinea-Bissau", "gy": "Guyana",
    "hk": "Hong Kong", "hn": "Honduras", "ht": "Haiti", "id": "Indonesia", "il": "Israel",
    "in": "India", "iq": "Iraq", "ir": "Iran", "jm": "Jamaica", "jo": "Jordan",
    "ke": "Kenya", "kg": "Kyrgyzstan", "kh": "Cambodia", "ki": "Kiribati", "km": "Comoros",
    "kn": "Saint Kitts and Nevis", "kr": "South Korea", "kw": "Kuwait", "la": "Laos",
    "lb": "Lebanon", "lc": "Saint Lucia", "li": "Liechtenstein", "lk": "Sri Lanka",
    "lr": "Liberia", "ls": "Lesotho", "ly": "Libya", "ma": "Morocco", "mg": "Madagascar",
    "mh": "Marshall Islands", "ml": "Mali", "mm": "Myanmar", "mn": "Mongolia",
    "mo": "Macao", "mr": "Mauritania", "mu": "Mauritius", "mv": "Maldives", "mw": "Malawi",
    "mx": "Mexico", "my": "Malaysia", "mz": "Mozambique", "na": "Namibia", "ne": "Niger",
    "ng": "Nigeria", "ni": "Nicaragua", "np": "Nepal", "nr": "Nauru", "nz": "New Zealand",
    "om": "Oman", "pa": "Panama", "pe": "Peru", "pg": "Papua New Guinea",
    "ph": "Philippines", "pk": "Pakistan", "pr": "Puerto Rico", "ps": "West Bank and Gaza",
    "pw": "Palau", "py": "Paraguay", "qa": "Qatar", "ru": "Russia", "rw": "Rwanda",
    "sa": "Saudi Arabia", "sb": "Solomon Islands", "sc": "Seychelles", "sd": "Sudan",
    "sg": "Singapore", "sl": "Sierra Leone", "sm": "San Marino", "sn": "Senegal",
    "so": "Somalia", "sr": "Suriname", "ss": "South Sudan", "st": "Sao Tome and Principe",
    "sv": "El Salvador", "sy": "Syria", "sz": "Eswatini", "td": "Chad", "tg": "Togo",
    "th": "Thailand", "tj": "Tajikistan", "tl": "Timor-Leste", "tm": "Turkmenistan",
    "tn": "Tunisia", "to": "Tonga", "tt": "Trinidad and Tobago", "tv": "Tuvalu",
    "tw": "Taiwan", "tz": "Tanzania", "ug": "Uganda", "uy": "Uruguay", "uz": "Uzbekistan",
    "vc": "Saint Vincent and the Grenadines", "ve": "Venezuela", "vn": "Vietnam",
    "vu": "Vanuatu", "ws": "Samoa", "xk": "Kosovo", "ye": "Yemen", "za": "South Africa",
    "zm": "Zambia", "zw": "Zimbabwe",
})

# The world layer's economies, by name (2026-11-19). Left out: Chad, Jordan,
# Guinea and Niger, which are words or first names before they are countries.
NAMES.update({
    "afghanistan": "af", "algeria": "dz", "andorra": "ad", "angola": "ao", "antigua": "ag",
    "argentina": "ar", "argentine": "ar", "argentinian": "ar", "armenia": "am",
    "aruba": "aw", "azerbaijan": "az", "bahamas": "bs", "bahrain": "bh", "bangladesh": "bd",
    "bangladeshi": "bd", "barbados": "bb", "belarus": "by", "belize": "bz", "benin": "bj",
    "bhutan": "bt", "bolivia": "bo", "botswana": "bw", "brazil": "br", "brazilian": "br",
    "brunei": "bn", "burkina faso": "bf", "burundi": "bi", "cabo verde": "cv",
    "cambodia": "kh", "cameroon": "cm", "chile": "cl", "chilean": "cl", "colombia": "co",
    "colombian": "co", "comoros": "km", "congo": "cd", "costa rica": "cr",
    "cote d'ivoire": "ci", "djibouti": "dj", "dominica": "dm", "dominican republic": "do",
    "dr congo": "cd", "ecuador": "ec", "egypt": "eg", "egyptian": "eg", "el salvador": "sv",
    "emirates": "ae", "equatorial guinea": "gq", "eritrea": "er", "eswatini": "sz",
    "ethiopia": "et", "fiji": "fj", "filipino": "ph", "gabon": "ga", "gambia": "gm",
    "gaza": "ps", "ghana": "gh", "ghanaian": "gh", "grenada": "gd", "guatemala": "gt",
    "guinea-bissau": "gw", "guyana": "gy", "haiti": "ht", "honduras": "hn",
    "hong kong": "hk", "india": "in", "indian": "in", "indonesia": "id", "indonesian": "id",
    "iran": "ir", "iranian": "ir", "iraq": "iq", "iraqi": "iq", "israel": "il",
    "israeli": "il", "ivory coast": "ci", "jamaica": "jm", "kenya": "ke", "kenyan": "ke",
    "kiribati": "ki", "korea": "kr", "korean": "kr", "kosovo": "xk", "kuwait": "kw",
    "kyrgyzstan": "kg", "laos": "la", "lebanon": "lb", "lesotho": "ls", "liberia": "lr",
    "libya": "ly", "liechtenstein": "li", "macao": "mo", "madagascar": "mg", "malawi": "mw",
    "malaysia": "my", "malaysian": "my", "maldives": "mv", "mali": "ml",
    "marshall islands": "mh", "mauritania": "mr", "mauritius": "mu", "mexican": "mx",
    "mexico": "mx", "micronesia": "fm", "mongolia": "mn", "moroccan": "ma", "morocco": "ma",
    "mozambique": "mz", "myanmar": "mm", "namibia": "na", "nauru": "nr", "nepal": "np",
    "new zealand": "nz", "nicaragua": "ni", "nigeria": "ng", "nigerian": "ng", "oman": "om",
    "pakistan": "pk", "pakistani": "pk", "palau": "pw", "palestine": "ps", "panama": "pa",
    "papua": "pg", "paraguay": "py", "peru": "pe", "peruvian": "pe", "philippine": "ph",
    "philippines": "ph", "puerto rico": "pr", "qatar": "qa", "russia": "ru",
    "russian": "ru", "rwanda": "rw", "saint lucia": "lc", "samoa": "ws", "san marino": "sm",
    "sao tome": "st", "saudi": "sa", "saudi arabia": "sa", "senegal": "sn",
    "seychelles": "sc", "sierra leone": "sl", "singapore": "sg", "singaporean": "sg",
    "solomon islands": "sb", "somalia": "so", "south africa": "za", "south african": "za",
    "south korea": "kr", "south sudan": "ss", "sri lanka": "lk", "st kitts": "kn",
    "st vincent": "vc", "sudan": "sd", "suriname": "sr", "syria": "sy", "taiwan": "tw",
    "taiwanese": "tw", "tajikistan": "tj", "tanzania": "tz", "thai": "th", "thailand": "th",
    "timor-leste": "tl", "togo": "tg", "tonga": "to", "trinidad": "tt", "tunisia": "tn",
    "turkmenistan": "tm", "tuvalu": "tv", "uae": "ae", "uganda": "ug", "uruguay": "uy",
    "uruguayan": "uy", "uzbekistan": "uz", "vanuatu": "vu", "venezuela": "ve",
    "venezuelan": "ve", "vietnam": "vn", "vietnamese": "vn", "yemen": "ye", "zambia": "zm",
    "zimbabwe": "zw",
})

# The IMF's and the OECD's ISO 3166 alpha-3 country codes → Bedrok's.
ISO3 = {
    "ABW": "aw", "AFG": "af", "AGO": "ao", "ALB": "al", "AND": "ad", "ARE": "ae",
    "ARG": "ar", "ARM": "am", "ATG": "ag", "AUS": "au", "AUT": "at", "AZE": "az",
    "BDI": "bi", "BEL": "be", "BEN": "bj", "BFA": "bf", "BGD": "bd", "BGR": "bg",
    "BHR": "bh", "BHS": "bs", "BIH": "ba", "BLR": "by", "BLZ": "bz", "BOL": "bo",
    "BRA": "br", "BRB": "bb", "BRN": "bn", "BTN": "bt", "BWA": "bw", "CAF": "cf",
    "CAN": "ca", "CHE": "ch", "CHL": "cl", "CHN": "cn", "CIV": "ci", "CMR": "cm",
    "COD": "cd", "COG": "cg", "COL": "co", "COM": "km", "CPV": "cv", "CRI": "cr",
    "CYP": "cy", "CZE": "cz", "DEU": "de", "DJI": "dj", "DMA": "dm", "DNK": "dk",
    "DOM": "do", "DZA": "dz", "ECU": "ec", "EGY": "eg", "ERI": "er", "ESP": "es",
    "EST": "ee", "ETH": "et", "FIN": "fi", "FJI": "fj", "FRA": "fr", "FSM": "fm",
    "GAB": "ga", "GBR": "uk", "GEO": "ge", "GHA": "gh", "GIN": "gn", "GMB": "gm",
    "GNB": "gw", "GNQ": "gq", "GRC": "el", "GRD": "gd", "GTM": "gt", "GUY": "gy",
    "HKG": "hk", "HND": "hn", "HRV": "hr", "HTI": "ht", "HUN": "hu", "IDN": "id",
    "IND": "in", "IRL": "ie", "IRN": "ir", "IRQ": "iq", "ISL": "is", "ISR": "il",
    "ITA": "it", "JAM": "jm", "JOR": "jo", "JPN": "jp", "KAZ": "kz", "KEN": "ke",
    "KGZ": "kg", "KHM": "kh", "KIR": "ki", "KNA": "kn", "KOR": "kr", "KOS": "xk",
    "KWT": "kw", "LAO": "la", "LBN": "lb", "LBR": "lr", "LBY": "ly", "LCA": "lc",
    "LIE": "li", "LKA": "lk", "LSO": "ls", "LTU": "lt", "LUX": "lu", "LVA": "lv",
    "MAC": "mo", "MAR": "ma", "MDA": "md", "MDG": "mg", "MDV": "mv", "MEX": "mx",
    "MHL": "mh", "MKD": "mk", "MLI": "ml", "MLT": "mt", "MMR": "mm", "MNE": "me",
    "MNG": "mn", "MOZ": "mz", "MRT": "mr", "MUS": "mu", "MWI": "mw", "MYS": "my",
    "NAM": "na", "NER": "ne", "NGA": "ng", "NIC": "ni", "NLD": "nl", "NOR": "no",
    "NPL": "np", "NRU": "nr", "NZL": "nz", "OMN": "om", "PAK": "pk", "PAN": "pa",
    "PER": "pe", "PHL": "ph", "PLW": "pw", "PNG": "pg", "POL": "pl", "PRI": "pr",
    "PRT": "pt", "PRY": "py", "QAT": "qa", "ROU": "ro", "RUS": "ru", "RWA": "rw",
    "SAU": "sa", "SDN": "sd", "SEN": "sn", "SGP": "sg", "SLB": "sb", "SLE": "sl",
    "SLV": "sv", "SMR": "sm", "SOM": "so", "SRB": "rs", "SSD": "ss", "STP": "st",
    "SUR": "sr", "SVK": "sk", "SVN": "si", "SWE": "se", "SWZ": "sz", "SYC": "sc",
    "SYR": "sy", "TCD": "td", "TGO": "tg", "THA": "th", "TJK": "tj", "TKM": "tm",
    "TLS": "tl", "TON": "to", "TTO": "tt", "TUN": "tn", "TUR": "tr", "TUV": "tv",
    "TWN": "tw", "TZA": "tz", "UGA": "ug", "UKR": "ua", "URY": "uy", "USA": "us",
    "UZB": "uz", "VCT": "vc", "VEN": "ve", "VNM": "vn", "VUT": "vu", "WBG": "ps",
    "WSM": "ws", "YEM": "ye", "ZAF": "za", "ZMB": "zm", "ZWE": "zw",
}

# Where an id suffix spells a place differently: OECD's ISO-3 codes and
# Eurostat's euro-area and EU aggregates.
_SUFFIX = {"usa": "us", "deu": "de", "fra": "fr", "ita": "it", "esp": "es", "gbr": "uk",
           "jpn": "jp", "chn": "cn", "ea19": "ea", "ea20": "ea", "ea21": "ea", "eu27": "eu",
           "cn": "cn"}

# Where an id prefix is an institution rather than a place: the ECB's rates are
# the euro area's, and a search for "euro area deposit rate" must find them.
_PREFIX = {"ecb": "ea"}

# ISO 3166 alpha-2 where it differs from the id code (the warnings carry ISO).
ISO2 = {**{c: c.upper() for c in CODES}, **{c: c.upper() for c in FAMILY_CODES},
        "uk": "GB", "el": "GR", "ea": "", "eu": ""}

# An exchange rate is about the country of its quote currency when that
# currency has one country: usdkzt is Kazakhstan's. The euro, the dollar and
# sterling are left out on purpose: "euro area exchange rate" must not bring
# every pair into a search.
_QUOTE_CURRENCY = {"kzt": "kz"}

# Sources whose series are not about a country.
_PLACELESS_SOURCES = {"worldbank", "cftc"}


def of_series(series_id: str, source: str | None = None) -> str | None:
    """The geography code a series is about, or None."""
    if source in _PLACELESS_SOURCES:
        return None
    sid = series_id.lower()
    if re.fullmatch(r"[a-z]{6}", sid) and sid[3:] in _QUOTE_CURRENCY:
        return _QUOTE_CURRENCY[sid[3:]]
    first = sid.split("_", 1)[0]
    if first in CODES:
        return first
    if first in _PREFIX:
        return _PREFIX[first]
    sid = re.sub(r"_eu27_2020$", "_eu27", sid)
    last = sid.rsplit("_", 1)[-1]
    if last in _SUFFIX:
        return _SUFFIX[last]
    return last if (last in CODES or last in FAMILY_CODES) and "_" in sid else None


def in_text(text: str) -> tuple[set[str], list[str]]:
    """(codes of the places named, the other words), two-word names first."""
    words = re.findall(r"[a-z0-9-]+", text.lower())
    codes, rest, i = set(), [], 0
    while i < len(words):
        pair = " ".join(words[i:i + 2])
        if i + 1 < len(words) and pair in NAMES:
            codes.add(NAMES[pair])
            i += 2
            continue
        if words[i] in NAMES:
            codes.add(NAMES[words[i]])
        else:
            rest.append(words[i])
        i += 1
    return codes, rest


def words_for_iso2(iso2: str) -> set[str]:
    """Every single word in NAMES that names the country with this ISO code."""
    codes = {c for c, iso in ISO2.items() if iso and iso == (iso2 or "").upper()}
    return {w for w, c in NAMES.items() if c in codes and " " not in w}
