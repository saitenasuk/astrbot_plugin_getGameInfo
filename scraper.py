"""游戏网站爬虫"""

import re
import asyncio
import logging
import time
from abc import ABC, abstractmethod
from typing import Optional
from urllib.parse import quote_plus

import httpx
from bs4 import BeautifulSoup, NavigableString, Tag

from .models import GameBrief

logger = logging.getLogger(__name__)


class BaseGameScraper(ABC):
    """游戏网站爬虫基类——所有站点爬虫必须实现此接口"""

    @property
    @abstractmethod
    def site_id(self) -> str: ...

    @property
    @abstractmethod
    def site_name(self) -> str: ...

    @property
    @abstractmethod
    def site_url(self) -> str: ...

    @abstractmethod
    async def fetch_list(self, page: int = 1) -> tuple[list[GameBrief], int]: ...

    @abstractmethod
    async def search(self, query: str, page: int = 1) -> tuple[list[GameBrief], int]: ...

    @abstractmethod
    def clean_title(self, raw: str) -> str: ...

    @abstractmethod
    async def close(self) -> None: ...


class OnlineFixScraper(BaseGameScraper):
    """online-fix.me 游戏网站爬虫（DLE CMS，Windows-1251）"""

    @property
    def site_id(self) -> str:
        return "onlinefix"

    @property
    def site_name(self) -> str:
        return "Online-Fix.me"

    @property
    def site_url(self) -> str:
        return "https://online-fix.me"

    _TITLE_STRIP_RE = re.compile(
        r"\s*(по сети|Online|online|Онлайн|онлайн|по сети\s*\(.*?\))$", re.IGNORECASE,
    )

    _LABEL_RELEASE_DATE = re.compile(
        r"(Дата\s*выхода\s*игры|Релиз\s*игры|Год\s*выпуска|Game\s*release)", re.IGNORECASE,
    )
    _LABEL_PLATFORM = re.compile(r"(Игра\s*через|Платформа|Play\s*via)", re.IGNORECASE)
    _LABEL_MODES = re.compile(r"(Режимы|Modes)", re.IGNORECASE)

    _UPDATE_DATE_RE = re.compile(
        r"Обновлено\s+(\d{1,2})\s+(января|февраля|марта|апреля|мая|июня|"
        r"июля|августа|сентября|октября|ноября|декабря)\s+(\d{4}),?\s*(\d{1,2}:\d{2})?",
        re.IGNORECASE,
    )
    _UPDATE_VER_RE = re.compile(r"Игра\s+обновлена\s+до\s+версии\s+([\d.]+)", re.IGNORECASE)

    _RU_MONTHS = {
        "января": 1, "февраля": 2, "марта": 3, "апреля": 4,
        "мая": 5, "июня": 6, "июля": 7, "августа": 8,
        "сентября": 9, "октября": 10, "ноября": 11, "декабря": 12,
    }

    _RU_TRANSLATIONS = {
        "Интернет": "网络", "интернет": "网络",
        "Кооператив": "合作模式", "кооператив": "合作模式",
        "Мультиплеер": "多人游戏", "мультиплеер": "多人游戏",
        "Одиночная игра": "单人游戏", "одиночная игра": "单人游戏",
        "Сетевая игра": "联网", "сетевая игра": "联网",
        "Internet": "网络", "internet": "网络",
        "Cooperative": "合作模式", "cooperative": "合作模式",
        "Multiplayer": "多人游戏", "multiplayer": "多人游戏",
        "Singleplayer": "单人游戏", "singleplayer": "单人游戏",
        "Online": "联网", "online": "联网",
        "LAN": "局域网", "lan": "局域网",
    }

    def __init__(self, user_agent: str = "", timeout: int = 30, request_delay: float = 1.5):
        self._user_agent = user_agent or "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        self._timeout = timeout
        self._request_delay = request_delay
        self._client: Optional[httpx.AsyncClient] = None
        self._last_request_time = 0.0

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self._timeout)
        return self._client

    async def close(self):
        if self._client:
            await self._client.aclose()
            self._client = None

    async def _rate_limit(self):
        now = time.monotonic()
        wait = self._last_request_time + self._request_delay - now
        if wait > 0:
            await asyncio.sleep(wait)
        self._last_request_time = time.monotonic()

    def _headers(self) -> dict:
        return {
            "User-Agent": self._user_agent,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
        }

    async def _fetch(self, url: str) -> str:
        await self._rate_limit()
        resp = await self.client.get(url, headers=self._headers(), follow_redirects=True)
        resp.raise_for_status()
        try:
            return resp.content.decode("utf-8")
        except UnicodeDecodeError:
            return resp.content.decode("windows-1251")

    def clean_title(self, raw: str) -> str:
        return self._TITLE_STRIP_RE.sub("", raw).strip()

    # ---- 公共 API ----

    async def fetch_list(self, page: int = 1) -> tuple[list[GameBrief], int]:
        url = f"{self.site_url}/" if page <= 1 else f"{self.site_url}/page/{page}/"
        html = await self._fetch(url)
        soup = BeautifulSoup(html, "lxml")
        games = []
        for article in soup.select("article.news"):
            g = self._parse_item(article)
            if g:
                games.append(g)
        total = self._parse_pagination(soup)
        return games, total

    async def search(self, query: str, page: int = 1) -> tuple[list[GameBrief], int]:
        search_start = page - 1
        url = (f"{self.site_url}/index.php?do=search&subaction=search"
               f"&search_start={search_start}&full_search=0&result_from=1"
               f"&story={quote_plus(query)}")
        html = await self._fetch(url)
        soup = BeautifulSoup(html, "lxml")
        games = []
        for el in soup.select("article.news, div.news, div.short-story"):
            g = self._parse_item(el)
            if g:
                games.append(g)
        total = self._parse_pagination(soup)
        return games, total

    _PLAYER_CLASS_RE = re.compile(r"^(coop|multi)\d*$")

    async def fetch_player_counts(self, game_url: str) -> tuple[int | None, int | None]:
        """从游戏详情页解析合作人数和多人人数。

        解析形如 <div class="coop1">КООПЕРАТИВ: 8</div> 的元素，
        返回 (合作人数, 多人人数)，未找到则为 None。
        """
        html = await self._fetch(game_url)
        soup = BeautifulSoup(html, "lxml")
        coop = None
        multi = None
        for div in soup.find_all("div", class_=self._PLAYER_CLASS_RE):
            text = div.get_text(strip=True)
            m = re.search(r"(\d+)", text)
            if not m:
                continue
            count = int(m.group(1))
            for cls_name in div.get("class", []):
                if cls_name.startswith("coop"):
                    coop = count
                    break
                elif cls_name.startswith("multi"):
                    multi = count
                    break
        return coop, multi

    # ---- 内部解析 ----

    def _parse_item(self, article: Tag) -> Optional[GameBrief]:
        try:
            img = article.select_one(".image img")
            poster_url = None
            if img:
                poster_url = img.get("data-src") or img.get("src")
                if poster_url and poster_url.startswith("/"):
                    poster_url = f"{self.site_url}{poster_url}"

            title_el = article.select_one("h2.title")
            if not title_el:
                return None
            title = self.clean_title(title_el.get_text(strip=True))

            link_el = (article.select_one("h2.title a") or article.select_one("a.big-link")
                       or article.select_one("a[href*='/games/']"))
            url = link_el.get("href", "") if link_el else ""
            game_id = self._extract_game_id(url)

            time_el = article.select_one(".info-date time")
            post_date = time_el.get("datetime", "")[:10] if time_el else None

            edit_el = article.select_one(".edit")
            raw_update = edit_el.get_text(strip=True) if edit_el else ""
            update_info = self._fmt_update(raw_update) if raw_update else None

            preview = article.select_one(".preview-text")
            release_date = self._parse_field(preview, self._LABEL_RELEASE_DATE)
            platforms_raw = self._parse_field(preview, self._LABEL_PLATFORM)
            modes_list = self._parse_modes(preview)

            platforms_list = []
            if platforms_raw:
                for p in re.split(r"[/,]", platforms_raw):
                    p = p.strip()
                    if p:
                        platforms_list.append(self._tr(p))

            return GameBrief(
                id=game_id, title=title, url=url, poster_url=poster_url,
                post_date=post_date, update_info=update_info,
                release_date=release_date, platforms=platforms_list, modes=modes_list,
            )
        except Exception:
            logger.debug("解析游戏条目失败", exc_info=True)
            return None

    @staticmethod
    def _extract_game_id(url: str) -> str:
        m = re.search(r"/(\d+)-[^/]+\.html$", url)
        return m.group(1) if m else url

    @staticmethod
    def _parse_pagination(soup: BeautifulSoup) -> int:
        nav = soup.select_one(".navigation, .pages, .pagination")
        if not nav:
            return 1
        nums = [int(a.get_text(strip=True)) for a in nav.select("a") if a.get_text(strip=True).isdigit()]
        return max(nums) if nums else 1

    @staticmethod
    def _parse_field(preview: Optional[Tag], label_re: re.Pattern) -> Optional[str]:
        if not preview:
            return None
        b_tags = preview.select("b")
        full_text = preview.get_text(" ", strip=True)
        for i, b in enumerate(b_tags):
            label = b.get_text(strip=True)
            if label_re.search(label):
                pos = full_text.find(label)
                if pos == -1:
                    continue
                start = pos + len(label)
                if i + 1 < len(b_tags):
                    next_label = b_tags[i + 1].get_text(strip=True)
                    end = full_text.find(next_label, start)
                    value = full_text[start:end].strip() if end != -1 else full_text[start:].strip()
                else:
                    value = full_text[start:].strip()
                return value if value else None
        return None

    @classmethod
    def _parse_modes(cls, preview: Optional[Tag]) -> list[str]:
        if not preview:
            return []
        for b in preview.select("b"):
            if cls._LABEL_MODES.search(b.get_text(strip=True)):
                modes = []
                current = ""
                for sibling in b.next_siblings:
                    if isinstance(sibling, Tag):
                        if sibling.name == "b":
                            break
                        if sibling.name == "br":
                            continue
                        if "fa-check" in (sibling.get("class") or []):
                            if current:
                                modes.append(f"{cls._tr(current.strip())} ✓")
                                current = ""
                        elif "fa-times" in (sibling.get("class") or []):
                            if current:
                                modes.append(f"{cls._tr(current.strip())} ✗")
                                current = ""
                        else:
                            inner = sibling.get_text(strip=True)
                            if inner:
                                current += inner
                    elif isinstance(sibling, NavigableString):
                        s = sibling.strip().strip("\\").strip()
                        if s:
                            current += s
                if current:
                    modes.append(cls._tr(current.strip()))
                return modes
        return []

    @classmethod
    def _tr(cls, text: str) -> str:
        if text in cls._RU_TRANSLATIONS:
            return cls._RU_TRANSLATIONS[text]
        words = text.split()
        return " ".join(cls._RU_TRANSLATIONS.get(w, w) for w in words)

    @classmethod
    def _fmt_update(cls, raw: str) -> str:
        result = raw
        m = cls._UPDATE_DATE_RE.search(result)
        if m:
            day, month_ru, year, time_str = m.group(1), m.group(2), m.group(3), m.group(4)
            month_num = cls._RU_MONTHS.get(month_ru.lower(), month_ru)
            cn_date = f"更新于{year}年{month_num}月{int(day)}日"
            if time_str:
                cn_date += f" {time_str}"
            result = cls._UPDATE_DATE_RE.sub(cn_date, result, count=1)
        result = cls._UPDATE_VER_RE.sub(r"游戏已更新至版本 \1", result)
        result = result.replace("Комментарии закрыты.", "评论已关闭。").replace("\xa0", " ")
        return result.strip()
