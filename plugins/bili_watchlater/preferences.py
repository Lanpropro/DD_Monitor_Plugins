"""Plugin-only preferences, persisted through the public host context."""
DEFAULTS = dict(default_view='grid', grid_columns=0, card_size='standard', autoplay=False,
                remember_state=True, auto_play=True, prefetch_next=False, video_preview=True)
CARD_WIDTHS = dict(compact=240, standard=300, large=360)


def read_preferences(context):
    reader = getattr(context, 'setting', None)
    values = {key: reader(key, default) if callable(reader) else default
              for key, default in DEFAULTS.items()}
    if values['default_view'] not in ('grid', 'list'):
        values['default_view'] = 'grid'
    if type(values['grid_columns']) is not int or values['grid_columns'] not in (0, 2, 3, 4, 5, 6):
        values['grid_columns'] = 0
    if not isinstance(values['card_size'], str) or values['card_size'] not in CARD_WIDTHS:
        values['card_size'] = 'standard'
    for key in ('autoplay', 'remember_state', 'auto_play', 'prefetch_next', 'video_preview'):
        values[key] = values[key] is True
    values['prefetch_next'] = values['prefetch_next'] and values['autoplay']
    return values


def save_preferences(context, values):
    for key in DEFAULTS:
        context.set_setting(key, values[key])
