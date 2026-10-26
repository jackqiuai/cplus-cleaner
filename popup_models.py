"""Shared popup data; legacy constructor order and persistent IDs stay stable."""
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Rule:
    id: str
    title: str
    window_class: str
    process_name: str
    width: int
    height: int
    pos: int
    default_enabled: bool = True
    source: str = 'legacy'
    action: str = 'close'
    raw_treat: str = 'close'
    supported: bool = True
    reason: str = ''
    description: str = ''
    definition: dict = field(default_factory=dict, repr=False)
    matcher: object = field(default=None, repr=False, compare=False)

    @property
    def label(self):
        return self.description or self.title or self.process_name or '广告弹窗'


@dataclass(frozen=True)
class Software:
    key: str
    name: str
    roots: tuple[str, ...]
    rules: tuple[Rule, ...]
    icon: bytes | None = None
    source: str = 'legacy'
    definition: dict = field(default_factory=dict, repr=False)
