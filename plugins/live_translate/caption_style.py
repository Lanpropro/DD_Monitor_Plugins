"""Small, persistent caption presentation preferences."""
from dataclasses import dataclass


@dataclass(frozen=True)
class CaptionStyle:
    enabled: bool = True
    mode: str = "both"
    lines: int = 2
    opacity: int = 65
    width: int = 80
    height: int = 18
    font_size: int = 24

    @classmethod
    def from_mapping(cls, value=None):
        if isinstance(value, cls):
            return value
        data = dict(value or {})
        mode = str(data.get("mode") or "both")
        if mode not in ("both", "original", "translation"):
            mode = "both"
        return cls(
            enabled=bool(data.get("enabled", True)),
            mode=mode,
            lines=_bounded_int(data, "lines", 2, 1, 4),
            opacity=_bounded_int(data, "opacity", 65, 0, 100),
            width=_bounded_int(data, "width", 80, 20, 100),
            height=_bounded_int(data, "height", 18, 8, 45),
            font_size=_bounded_int(data, "font_size", 24, 10, 64),
        )

    def mapping(self):
        return {
            "enabled": self.enabled,
            "mode": self.mode,
            "lines": self.lines,
            "opacity": self.opacity,
            "width": self.width,
            "height": self.height,
            "font_size": self.font_size,
        }

    def text(self, original, translated):
        original = str(original or "").strip()
        translated = str(translated or "").strip()
        if self.mode == "original":
            return original, ""
        if self.mode == "translation":
            return "", translated
        return original, translated


def _bounded_int(data, key, default, low, high):
    try:
        value = int(data.get(key, default))
    except (TypeError, ValueError):
        value = default
    return max(low, min(high, value))
