"""Keep the user's existing player-count and scraper changes covered."""

import unittest
from unittest.mock import AsyncMock

from tests.support import main


class ScraperRegressionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.scraper = main.OnlineFixScraper()
        self.addAsyncCleanup(self.scraper.close)

    async def test_user_player_count_feature_with_suffixed_classes(self):
        self.scraper._fetch = AsyncMock(
            return_value=(
                '<div class="coop1">КООПЕРАТИВ: 4</div>'
                '<div class="multi2">МУЛЬТИПЛЕЕР: 8</div>'
            )
        )
        self.assertEqual(
            await self.scraper.fetch_player_counts("https://example.com/game"), (4, 8)
        )

    async def test_unknown_and_zero_counts_remain_distinct(self):
        self.scraper._fetch = AsyncMock(
            return_value='<div class="coop">КООПЕРАТИВ: 0</div>'
        )
        self.assertEqual(
            await self.scraper.fetch_player_counts("https://example.com/game"),
            (0, None),
        )
        self.scraper._fetch.return_value = '<div class="multi">Unknown</div>'
        self.assertEqual(
            await self.scraper.fetch_player_counts("https://example.com/game"),
            (None, None),
        )

    async def test_existing_list_parser_uses_fixture_not_live_site(self):
        self.scraper._fetch = AsyncMock(
            return_value="""
            <html><article class="news">
              <div class="image"><a href="/games/42-example.html"><img src="/poster.jpg"></a></div>
              <h2 class="title"><a href="/games/42-example.html">Example по сети</a></h2>
            </article></html>
        """
        )
        games, total = await self.scraper.fetch_list(1)
        self.assertEqual(len(games), 1)
        self.assertEqual(games[0].id, "42")
        self.assertEqual(games[0].title, "Example")
        self.assertEqual(games[0].poster_url, "https://online-fix.me/poster.jpg")
        self.assertGreaterEqual(total, 1)

    async def test_closing_shared_scraper_client(self):
        client = self.scraper.client
        await self.scraper.close()
        self.assertTrue(client.is_closed)
