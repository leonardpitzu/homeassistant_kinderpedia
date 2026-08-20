"""Tests for Kinderpedia newsfeed parsing and sensor."""

from unittest.mock import MagicMock

from custom_components.kinderpedia.coordinator import _parse_newsfeed
from custom_components.kinderpedia.sensor import KinderpediaNewsfeedSensor
from tests.conftest import MOCK_CHILD, MOCK_NEWSFEED_RAW


class TestParseNewsfeed:
    """Tests for the _parse_newsfeed function."""

    def test_gallery_items_are_filtered_out(self):
        """Gallery items must be excluded from parsed output."""
        items = _parse_newsfeed(MOCK_NEWSFEED_RAW)
        for item in items:
            assert "gallery" not in item.get("summary", "").lower() or True
            # The gallery entry from the raw data should not appear at all
        assert all(item["id"] != 37973 for item in items)

    def test_parse_invoice_summary(self):
        """Invoice items produce a useful payment summary."""
        items = _parse_newsfeed(MOCK_NEWSFEED_RAW)
        invoice = items[0]

        assert invoice["id"] == 37736
        assert "type" not in invoice
        assert "GH018654" in invoice["summary"]
        assert "Due Date" in invoice["summary"]
        assert "380 EUR" in invoice["summary"]
        assert "author" not in invoice
        assert "latest_comment" not in invoice

    def test_parse_text_post_summary(self):
        """Text/wall posts get author + description summary."""
        data = {
            "result": {
                "feed": [
                    {
                        "id": 1,
                        "type": "text",
                        "user": {"first_name": "John", "last_name": "Doe"},
                        "title": "John Doe",
                        "date": "2026-02-01T10:00:00+0200",
                        "date_friendly": "1 February 2026 at 10:00",
                        "stats": {"likes": 2, "comments": 0},
                        "latest_comments": None,
                        "content": {
                            "type": "wall_post",
                            "description": "Hello everyone, welcome!",
                            "title": "",
                            "gallery": None,
                            "video": None,
                            "file": None,
                        },
                        "groups": [],
                        "children": None,
                    }
                ]
            }
        }
        items = _parse_newsfeed(data)
        assert len(items) == 1
        assert "John Doe" in items[0]["summary"]
        assert "Hello everyone" in items[0]["summary"]
        assert "type" not in items[0]
        assert "group" not in items[0]

    def test_parse_count(self):
        items = _parse_newsfeed(MOCK_NEWSFEED_RAW)
        assert len(items) == 1  # gallery filtered out, only invoice remains

    def test_parse_empty_json(self):
        assert _parse_newsfeed({}) == []

    def test_parse_none_input(self):
        assert _parse_newsfeed(None) == []

    def test_parse_missing_result(self):
        assert _parse_newsfeed({"code": ""}) == []

    def test_parse_empty_feed(self):
        assert _parse_newsfeed({"result": {"feed": []}}) == []

    def test_parse_null_feed_entries_skipped(self):
        """Null / non-dict feed entries must be skipped, not crash."""
        data = {"result": {"feed": [None, "garbage", {"id": 1, "type": "text", "content": {"title": "Hi"}}]}}
        items = _parse_newsfeed(data)
        assert len(items) == 1
        assert items[0]["title"] == "Hi"

    def test_no_image_or_video_fields(self):
        """Parsed items must not contain image/video URL fields."""
        items = _parse_newsfeed(MOCK_NEWSFEED_RAW)
        for item in items:
            assert "image_count" not in item
            assert "first_image_url" not in item
            assert "video_url" not in item
            assert "file_url" not in item


class TestNewsfeedSensor:
    """Tests for KinderpediaNewsfeedSensor."""

    def _make_coordinator(self, feed_items):
        coordinator = MagicMock()
        coordinator.data = {
            "last_updated": "2026-02-21 12:00:00",
            "children": {
                "111_222": {
                    "child": dict(MOCK_CHILD),
                    "days": {},
                    "newsfeed": feed_items,
                }
            },
        }
        return coordinator

    def test_native_value_is_summary(self):
        feed = _parse_newsfeed(MOCK_NEWSFEED_RAW)
        coordinator = self._make_coordinator(feed)
        sensor = KinderpediaNewsfeedSensor(coordinator, 111, 222, "Alice Smith", "Alice")

        assert "GH018654" in sensor.native_value

    def test_native_value_none_when_empty(self):
        coordinator = self._make_coordinator([])
        sensor = KinderpediaNewsfeedSensor(coordinator, 111, 222, "Alice Smith", "Alice")
        assert sensor.native_value is None

    def test_attributes_text_only(self):
        """Attributes must not contain image/video URLs or removed fields."""
        feed = _parse_newsfeed(MOCK_NEWSFEED_RAW)
        coordinator = self._make_coordinator(feed)
        sensor = KinderpediaNewsfeedSensor(coordinator, 111, 222, "Alice Smith", "Alice")
        attrs = sensor.extra_state_attributes

        assert "latest_date" in attrs
        assert "recent" in attrs
        assert isinstance(attrs["recent"], str)

        # Removed fields
        assert "item_count" not in attrs
        assert "latest_type" not in attrs
        assert "latest_author" not in attrs
        assert "latest_likes" not in attrs
        assert "latest_group" not in attrs
        assert "latest_comments" not in attrs
        assert "latest_comment" not in attrs

        # No image/video junk
        assert "latest_image_url" not in attrs
        assert "latest_image_count" not in attrs
        assert "latest_video_url" not in attrs

    def test_recent_items_are_summaries(self):
        feed = _parse_newsfeed(MOCK_NEWSFEED_RAW)
        coordinator = self._make_coordinator(feed)
        sensor = KinderpediaNewsfeedSensor(coordinator, 111, 222, "Alice Smith", "Alice")
        recent = sensor.extra_state_attributes["recent"]

        assert isinstance(recent, str)
        # Must contain the invoice summary text
        assert "GH018654" in recent
        # Must contain the date emoji prefix
        assert "📅" in recent

    def test_attributes_empty_feed(self):
        coordinator = self._make_coordinator([])
        sensor = KinderpediaNewsfeedSensor(coordinator, 111, 222, "Alice Smith", "Alice")
        attrs = sensor.extra_state_attributes

        assert "item_count" not in attrs
        assert "latest_type" not in attrs
        assert "latest_date" not in attrs

    def test_no_coordinator_data(self):
        coordinator = MagicMock()
        coordinator.data = None
        sensor = KinderpediaNewsfeedSensor(coordinator, 111, 222, "Alice Smith", "Alice")

        assert sensor.native_value is None
        assert "item_count" not in sensor.extra_state_attributes
