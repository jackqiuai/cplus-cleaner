"""Public feedback destinations and a small, explicit environment summary."""
import platform

from cplus_cleaner.application import preferences


ISSUES_URL = "https://github.com/jackqiuai/cplus-cleaner/issues"
FEEDBACK_URLS = {
    "bug": f"{ISSUES_URL}/new?template=bug_report.yml",
    "feature": f"{ISSUES_URL}/new?template=feature_request.yml",
    "issues": ISSUES_URL,
}


def environment_info():
    return (f"CPlus Cleaner v{preferences.VERSION}\n"
            f"操作系统：{platform.system()} {platform.release()} ({platform.version()})\n"
            f"系统架构：{platform.machine() or '未知'}")

# Purpose: Feedback dialog and issue link
