"""On-demand version checks against this project's public stable GitHub Release."""
from dataclasses import dataclass
import json
import re
from urllib import error, parse, request

from cplus_cleaner.application import preferences


API_URL = 'https://api.github.com/repos/jackqiuai/cplus-cleaner/releases/latest'
RELEASES_URL = 'https://github.com/jackqiuai/cplus-cleaner/releases/latest'
MAX_RESPONSE = 1024*1024


class UpdateError(ValueError):
    pass


@dataclass(frozen=True)
class ReleaseInfo:
    version: str
    available: bool
    url: str


def version_tuple(value):
    if not isinstance(value, str) or not re.fullmatch(r'[vV]?\d+(?:\.\d+){1,3}', value.strip()):
        raise UpdateError('无法识别发布版本号，请查看发布页。')
    parts = tuple(int(part) for part in value.strip().lstrip('vV').split('.'))
    return parts + (0,)*(4-len(parts))


def check_latest(current=None, *, opener=None):
    current = preferences.VERSION if current is None else current
    current_parts = version_tuple(current)
    message = request.Request(API_URL, headers={
        'Accept': 'application/vnd.github+json',
        'User-Agent': f'CPlusCleaner/{current}',
        'X-GitHub-Api-Version': '2022-11-28',
    })
    try:
        with (opener or request.urlopen)(message, timeout=10) as response:
            raw = response.read(MAX_RESPONSE+1)
        if len(raw) > MAX_RESPONSE:
            raise UpdateError('发布信息格式无效，请查看发布页。')
        release = json.loads(raw)
    except error.HTTPError as exception:
        exception.close()
        if exception.code == 404:
            raise UpdateError('暂未找到正式发布版本。') from exception
        if exception.code in (403, 429):
            raise UpdateError('GitHub 请求受限，请稍后再试或查看发布页。') from exception
        raise UpdateError('检查更新失败，请稍后重试。') from exception
    except (error.URLError, TimeoutError, OSError):
        raise UpdateError('无法连接 GitHub，请检查网络后重试。') from None
    except (UnicodeError, json.JSONDecodeError, TypeError):
        raise UpdateError('发布信息格式无效，请查看发布页。') from None
    if (not isinstance(release, dict) or release.get('draft') is not False
            or release.get('prerelease') is not False):
        raise UpdateError('暂未找到正式发布版本。')
    latest = release.get('tag_name')
    latest_parts = version_tuple(latest)
    # Derive a project-owned destination; never open an arbitrary API-supplied URL.
    url = 'https://github.com/jackqiuai/cplus-cleaner/releases/tag/' + parse.quote(latest.strip(), safe='')
    return ReleaseInfo(latest.strip().lstrip('vV'), latest_parts > current_parts, url)


# Purpose: Public GitHub stable-release lookup and numeric version comparison
