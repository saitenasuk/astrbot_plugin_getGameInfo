"""数据模型"""

from dataclasses import dataclass, field


@dataclass
class GameBrief:
    id: str
    title: str
    url: str
    site_id: str = "onlinefix"
    poster_url: str | None = None
    post_date: str | None = None
    update_info: str | None = None
    release_date: str | None = None
    platforms: list[str] = field(default_factory=list)
    modes: list[str] = field(default_factory=list)
    coop_players: int | None = None
    multi_players: int | None = None
    views: int | None = None
    comments_info: str | None = None

