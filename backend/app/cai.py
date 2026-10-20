from decimal import Decimal

CAI_FORMATS = {
    'Media carta': ('5.5', '8.5'),
    'Cuarto de carta': ('4.25', '5.5'),
    '8.5 × 7.25': ('8.5', '7.25'),
    '8.5 × 8.5': ('8.5', '8.5'),
    'Carta': ('8.5', '11'),
}
CAI_GAMES = {25, 50}
CAI_PAPERS = {'Bond', 'Químico'}
CAI_COPIES = {1, 2, 3}
CAI_BINDINGS = {'Engrapado', 'Pegado', 'Hojas sueltas'}
CAI_PRODUCT = 'Talonarios de factura CAI'


def cai_summary(item):
    """Derived finished-product counts; commercial quantity remains talonarios."""
    if item.get('product') != CAI_PRODUCT:
        return None
    specs = item.get('specs') or {}
    try:
        pads = Decimal(str(item['quantity']))
        games = pads * int(specs['games_per_pad'])
        sheets_per_game = 1 + int(specs['additional_copies'])
    except (KeyError, TypeError, ValueError, ArithmeticError):
        return None
    return {
        'total_games': int(games),
        'sheets_per_game': sheets_per_game,
        'finished_sheets': int(games) * sheets_per_game,
    }
