"""Shared interaction values; no platform requests or sessions."""
import re
from dataclasses import dataclass, field


@dataclass(frozen=True)
class ChatReceipt:
    uid: str
    uname: str
    text: str


class InteractionError(ValueError):
    pass


class LoginRequired(InteractionError):
    pass


def room_number(room):
    value = str(room or "")
    if value.startswith("douyu:"):
        value = value[6:]
    if not re.fullmatch(r"[1-9][0-9]{0,19}", value):
        raise InteractionError("斗鱼房间号无效")
    return value


@dataclass(frozen=True)
class Gift:
    id: int
    name: str
    price: int
    currency: str
    skin_id: int
    maximum: int
    disabled: str = ""
    image: str = field(default="", compare=False)

    def cost(self, count=1):
        value = self.price * count
        return f"{format_yuchi(value)} 鱼翅" if self.currency == "YUCHI" else f"{value} 鱼丸"


def format_yuchi(value):
    whole, fraction = divmod(value, 100)
    return str(whole) if not fraction else f"{whole}.{fraction:02d}".rstrip("0")


@dataclass(frozen=True)
class GiftTab:
    id: int
    name: str
    items: tuple


@dataclass(frozen=True)
class Wallet:
    yuchi: int  # hundredths of a fish fin, matching priceInfo.price
    yuwan: int


@dataclass(frozen=True)
class GiftPanel:
    gifts: tuple
    tabs: tuple
    wallet: Wallet | None = None
    wallet_error: str = ""
