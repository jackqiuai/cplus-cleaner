"""Public donation destinations and owner-supplied QR display regions."""
from dataclasses import dataclass


KOFI_URL = "https://ko-fi.com/qiujack"


@dataclass(frozen=True)
class DonationCode:
    key: str
    name: str
    asset: str
    # Show the QR and its quiet zone; keep the complete supplied image for saving.
    viewport: tuple[int, int, int, int]


QR_CODES = (
    DonationCode("wechat", "微信支付", "donation/wechat.png", (285, 358, 834, 907)),
)

# Purpose: Public donation link and owner-supplied Chinese payment QR definitions
