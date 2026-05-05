"""
Single source of truth for user-facing signal tier labels.
Internal DB column values (STRONG_BUY, BUY, etc.) are unchanged.
Import tier_label() / tier_short() wherever display strings are needed.
"""

SIGNAL_TIERS = {
    'STRONG_BUY':  {'label': 'Very Strong Signal',    'short': 'Very Strong',  'colour': 'green',       'min_score': 75},
    'BUY':         {'label': 'Strong Signal',          'short': 'Strong',       'colour': 'light_green', 'min_score': 65},
    'STRONG_HOLD': {'label': 'Stable Signal',          'short': 'Stable',       'colour': 'cyan',        'min_score': 55},
    'HOLD':        {'label': 'Neutral Signal',         'short': 'Neutral',      'colour': 'grey',        'min_score': 45},
    'WEAK_HOLD':   {'label': 'Soft Signal',            'short': 'Soft',         'colour': 'amber',       'min_score': 35},
    'SELL':        {'label': 'Bearish Signal',         'short': 'Bearish',      'colour': 'orange',      'min_score': 25},
    'STRONG_SELL': {'label': 'Strong Bearish Signal',  'short': 'Very Bearish', 'colour': 'red',         'min_score': 0},
}


def tier_label(rating: str) -> str:
    return SIGNAL_TIERS.get(rating, {}).get('label', rating)


def tier_short(rating: str) -> str:
    return SIGNAL_TIERS.get(rating, {}).get('short', rating)


def tier_colour(rating: str) -> str:
    return SIGNAL_TIERS.get(rating, {}).get('colour', 'grey')
