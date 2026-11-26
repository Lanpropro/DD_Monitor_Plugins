"""Keep the last API selection separate from the local service's model alias."""
from dataclasses import asdict
from .options import with_bundled_defaults


def panel_settings(context):
    values = dict(context.setting('options', {}) or {})
    profile = context.setting('api_profile', None)
    if isinstance(profile, dict):
        values.update(endpoint=str(profile.get('endpoint') or ''), model=str(profile.get('model') or ''))
    elif not values.get('endpoint') and values.get('model') == 'qwen3:0.6b':
        values['model'] = ''
    return with_bundled_defaults(values)


def save_options(context, options):
    values = asdict(options)
    values.pop('glossary', None)
    values.pop('glossary_language', None)
    context.set_setting('options', values)
    if options.translation_mode == 'api':
        context.set_setting('api_profile', dict(endpoint=options.endpoint, model=options.model))
