"""US state and territory codes. Fifty-state pages use US_STATES; NASR and NPIAS also have territories."""

from __future__ import annotations

US_STATES = {
    "AL": "Alabama",
    "AK": "Alaska",
    "AZ": "Arizona",
    "AR": "Arkansas",
    "CA": "California",
    "CO": "Colorado",
    "CT": "Connecticut",
    "DE": "Delaware",
    "FL": "Florida",
    "GA": "Georgia",
    "HI": "Hawaii",
    "ID": "Idaho",
    "IL": "Illinois",
    "IN": "Indiana",
    "IA": "Iowa",
    "KS": "Kansas",
    "KY": "Kentucky",
    "LA": "Louisiana",
    "ME": "Maine",
    "MD": "Maryland",
    "MA": "Massachusetts",
    "MI": "Michigan",
    "MN": "Minnesota",
    "MS": "Mississippi",
    "MO": "Missouri",
    "MT": "Montana",
    "NE": "Nebraska",
    "NV": "Nevada",
    "NH": "New Hampshire",
    "NJ": "New Jersey",
    "NM": "New Mexico",
    "NY": "New York",
    "NC": "North Carolina",
    "ND": "North Dakota",
    "OH": "Ohio",
    "OK": "Oklahoma",
    "OR": "Oregon",
    "PA": "Pennsylvania",
    "RI": "Rhode Island",
    "SC": "South Carolina",
    "SD": "South Dakota",
    "TN": "Tennessee",
    "TX": "Texas",
    "UT": "Utah",
    "VT": "Vermont",
    "VA": "Virginia",
    "WA": "Washington",
    "WV": "West Virginia",
    "WI": "Wisconsin",
    "WY": "Wyoming",
}

TERRITORIES = {
    "AS": "American Samoa",
    "GU": "Guam",
    "MP": "Pacific Islands",
    "PR": "Puerto Rico",
    "VI": "Virgin Islands",
    "DC": "District of Columbia",
}

STATE_NAME_TO_CODE = {name: code for code, name in US_STATES.items()}
STATE_NAME_TO_CODE.update({name: code for code, name in TERRITORIES.items()})

# FAA Airports regions. Used to stratify eval samples, not as a catalog field.
FAA_AIRPORTS_REGIONS = {
    "alaskan": ("AK",),
    "central": ("IA", "KS", "MO", "NE"),
    "eastern": ("DC", "DE", "MD", "NJ", "NY", "PA", "VA", "WV"),
    "great_lakes": ("IL", "IN", "MI", "MN", "ND", "OH", "SD", "WI"),
    "new_england": ("CT", "ME", "MA", "NH", "RI", "VT"),
    "northwest_mountain": ("CO", "ID", "MT", "OR", "UT", "WA", "WY"),
    "southern": ("AL", "FL", "GA", "KY", "MS", "NC", "SC", "TN"),
    "southwest": ("AR", "LA", "NM", "OK", "TX"),
    "western_pacific": ("AZ", "CA", "HI", "NV"),
}
STATE_TO_FAA_REGION = {
    code: region
    for region, codes in FAA_AIRPORTS_REGIONS.items()
    for code in codes
}
