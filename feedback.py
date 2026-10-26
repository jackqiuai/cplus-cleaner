"""Public feedback destinations and a small, explicit environment summary."""
import platform

import preferences


# Fill these with the repository's Issue Form / Issues URLs after publication.
FEEDBACK_URLS = {"bug": "", "feature": "", "issues": ""}


def environment_info():
    return (f"CPlus Cleaner v{preferences.VERSION}\n"
            f"操作系统：{platform.system()} {platform.release()} ({platform.version()})\n"
            f"系统架构：{platform.machine() or '未知'}")
