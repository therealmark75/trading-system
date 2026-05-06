"""
User tier definitions — single source of truth for all feature limits.
All gated features reference this file; never hardcode numeric limits.

Invariant: Default new user tier is 'free'. markn is 'elite' for dev access.
"""

USER_TIERS = {
    'free': {
        'display_name':    'Free',
        'description':     '7-day trial of basic features',
        'watchlist_limit': 2,
        'order':           0,
    },
    'starter': {
        'display_name':    'Starter',
        'description':     'Full signals, basic alerts',
        'watchlist_limit': 5,
        'order':           1,
    },
    'pro': {
        'display_name':    'Pro',
        'description':     'All features, full backtest, API read',
        'watchlist_limit': 20,
        'order':           2,
    },
    'elite': {
        'display_name':    'Elite',
        'description':     'Everything plus API write and early access',
        'watchlist_limit': None,  # None = unlimited
        'order':           3,
    },
}


def get_tier(tier_key: str) -> dict:
    """Return tier config, defaulting to 'free' if key is invalid or None."""
    return USER_TIERS.get(tier_key or 'free', USER_TIERS['free'])


def watchlist_limit(tier_key: str):
    """Return watchlist limit for a tier. None = unlimited."""
    return get_tier(tier_key)['watchlist_limit']


def can_create_watchlist(tier_key: str, current_count: int) -> bool:
    """True if user can create another watchlist."""
    limit = watchlist_limit(tier_key)
    if limit is None:
        return True
    return current_count < limit


def next_tier(tier_key: str):
    """Return the key of the next tier above this one, or None if already at max."""
    current_order = get_tier(tier_key).get('order', 0)
    for key, cfg in USER_TIERS.items():
        if cfg['order'] == current_order + 1:
            return key
    return None
