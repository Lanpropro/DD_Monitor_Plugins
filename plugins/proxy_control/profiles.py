"""Named local service profiles; keep credentials outside the plugin package."""
from dataclasses import asdict, dataclass, field, replace
import json
import os
from pathlib import Path
from uuid import uuid4
from .metadata import display_name


@dataclass(frozen=True)
class Profile:
    id: str
    name: str
    source: str
    profile: str
    node: str = ''
    nodes: dict = field(default_factory=dict)


def load_profiles(directory, options):
    path = Path(directory) / 'profiles.json'
    try:
        records = json.loads(path.read_text(encoding='utf-8'))
        profiles = [Profile(**record) for record in records]
    except (OSError, ValueError, TypeError):
        profiles = []
    if not profiles and options.profile:
        profiles = [Profile(options.profile_id or uuid4().hex, options.profile_name,
                            options.source, options.profile, options.node)]
    return [replace(profile, name=display_name(profile.source, profile.profile))
            if profile.name in ('', '我的配置') else profile for profile in profiles]


def save_profiles(directory, profiles):
    path = Path(directory) / 'profiles.json'
    temporary = path.with_suffix('.json.tmp')
    temporary.write_text(json.dumps([asdict(profile) for profile in profiles], ensure_ascii=False), encoding='utf-8')
    os.chmod(temporary, 0o600)
    temporary.replace(path)
