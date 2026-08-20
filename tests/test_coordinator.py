"""Tests for the Kinderpedia coordinator and timeline parser."""

from custom_components.kinderpedia.coordinator import _parse_timeline as _parse_by_date


def _parse_timeline(raw):
    """Parse and re-key by weekday name so assertions stay readable."""
    return {day["name"]: day for day in _parse_by_date(raw).values()}


def _make_week(monday_data, extra_days=None):
    """Build a 7-day timeline raw dict. Only Monday gets custom data."""
    days = {
        "2026-02-09": monday_data,   # Monday
        "2026-02-10": {"data": []},  # Tuesday
        "2026-02-11": {"data": []},  # Wednesday
        "2026-02-12": {"data": []},  # Thursday
        "2026-02-13": {"data": []},  # Friday
        "2026-02-14": {"data": []},  # Saturday
        "2026-02-15": {"data": []},  # Sunday
    }
    if extra_days:
        days.update(extra_days)
    return {"result": {"dailytimeline": {"days": days}}}


class TestParseTimeline:
    """Tests for the _parse_timeline helper."""

    def test_full_timeline(self):
        """Test parsing a complete timeline with all data types."""
        raw = _make_week({
            "data": [
                {"id": "checkin", "subtitle": "08:15 - 16:30"},
                {"id": "nap", "subtitle": "1 h and 30 min"},
                {
                    "id": "food_1",
                    "details": {
                        "food": {
                            "meals": [
                                {
                                    "type": "md",
                                    "percent": 80,
                                    "menus": [{"name": "Cereal"}],
                                    "totals": {"kcal": 200, "weight": 150},
                                }
                            ]
                        }
                    },
                },
            ]
        })

        result = _parse_timeline(raw)

        assert "monday" in result
        monday = result["monday"]
        assert monday["date"] == "2026-02-09"
        assert monday["checkin"] == "08:15 - 16:30"
        assert monday["nap"] == "1 h and 30 min"
        assert monday["nap_duration"] == 90
        assert monday["breakfast_items"] == ["Cereal"]
        assert monday["breakfast_kcal"] == 200
        assert monday["breakfast_weight"] == 150
        assert monday["breakfast_percent"] == 80

    def test_all_seven_days_parsed(self):
        """All 7 days from the API are parsed, keyed by weekday name."""
        raw = _make_week({"data": []})
        result = _parse_timeline(raw)

        assert len(result) == 7
        for day in ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]:
            assert day in result
        assert result["saturday"]["date"] == "2026-02-14"
        assert result["sunday"]["date"] == "2026-02-15"

    def test_days_are_keyed_by_iso_date(self):
        """The parser keys days by ISO date, with the weekday kept as a field."""
        parsed = _parse_by_date(_make_week({"data": []}))

        assert set(parsed) == {
            f"2026-02-{day:02d}" for day in range(9, 16)
        }
        assert parsed["2026-02-09"]["name"] == "monday"

    def test_empty_days(self):
        """Test that empty day data returns defaults."""
        raw = _make_week({"data": []})
        result = _parse_timeline(raw)

        assert result["monday"]["checkin"] == "unknown"
        assert result["monday"]["nap"] == "unknown"

    def test_nap_hours_and_minutes(self):
        """Test nap duration parsing with hours and minutes."""
        raw = _make_week({
            "data": [{"id": "nap", "subtitle": "2 h and 15 min"}]
        })
        result = _parse_timeline(raw)
        assert result["monday"]["nap_duration"] == 135

    def test_nap_minutes_only(self):
        """Test nap duration parsing with only minutes."""
        raw = _make_week({
            "data": [{"id": "nap", "subtitle": "45 min"}]
        })
        result = _parse_timeline(raw)
        assert result["monday"]["nap_duration"] == 45

    def test_lunch_percent_averages_mp_and_mp2(self):
        """Test that mp and mp2 percentages are averaged for lunch."""
        raw = _make_week({
            "data": [
                {
                    "id": "food_1",
                    "details": {
                        "food": {
                            "meals": [
                                {
                                    "type": "mp",
                                    "percent": 80,
                                    "menus": [{"name": "Soup"}],
                                    "totals": {"kcal": 300, "weight": 200},
                                },
                                {
                                    "type": "mp2",
                                    "percent": 60,
                                    "menus": [{"name": "Pasta"}],
                                    "totals": {"kcal": 350, "weight": 250},
                                },
                            ]
                        }
                    },
                }
            ]
        })
        result = _parse_timeline(raw)
        assert result["monday"]["lunch_percent"] == 70.0

    def test_empty_json(self):
        """Test parsing with empty JSON returns empty dict."""
        result = _parse_timeline({})
        assert len(result) == 0

    def test_none_input(self):
        """Test parsing with None-like input returns empty dict."""
        result = _parse_timeline(None)
        assert len(result) == 0

    def test_missing_result_key(self):
        """Test parsing when result key is missing returns empty dict."""
        result = _parse_timeline({"other_key": {}})
        assert len(result) == 0

    def test_fewer_than_seven_days(self):
        """Test timeline with fewer than 7 days only returns those days."""
        raw = {
            "result": {
                "dailytimeline": {
                    "days": {
                        "2026-02-09": {"data": []},
                        "2026-02-10": {"data": []},
                    }
                }
            }
        }
        result = _parse_timeline(raw)
        assert len(result) == 2
        assert "monday" in result
        assert "tuesday" in result
        assert result["monday"]["date"] == "2026-02-09"

    def test_null_data_field(self):
        """Test when day data field is None."""
        raw = _make_week(None)
        result = _parse_timeline(raw)
        assert result["monday"]["checkin"] == "unknown"

    def test_null_timeline_items_skipped(self):
        """Null / non-dict entries in a day's data list must be skipped."""
        raw = _make_week({
            "data": [None, "garbage", {"id": "checkin", "subtitle": "08:15 - 16:30"}]
        })
        result = _parse_timeline(raw)
        assert result["monday"]["checkin"] == "08:15 - 16:30"

    def test_null_meal_and_menu_entries_skipped(self):
        """Null meals and null menu items must not break food parsing."""
        raw = _make_week({
            "data": [
                {
                    "id": "food_1",
                    "details": {
                        "food": {
                            "meals": [
                                None,
                                {
                                    "type": "md",
                                    "percent": 80,
                                    "menus": [None, {"name": "Cereal"}],
                                    "totals": {"kcal": 200, "weight": 150},
                                },
                            ]
                        }
                    },
                }
            ]
        })
        result = _parse_timeline(raw)
        assert result["monday"]["breakfast_items"] == ["Cereal"]
        assert result["monday"]["breakfast_percent"] == 80

    def test_nap_unparseable_format(self):
        """Test nap with an unrecognised format gets duration 0."""
        raw = _make_week({
            "data": [{"id": "nap", "subtitle": "a long while"}]
        })
        result = _parse_timeline(raw)
        assert result["monday"]["nap_duration"] == 0

    def test_weekday_derived_from_date(self):
        """Weekday key is derived from the actual date, not position."""
        raw = {
            "result": {
                "dailytimeline": {
                    "days": {
                        "2026-02-11": {  # This is a Wednesday
                            "data": [{"id": "checkin", "subtitle": "09:00 - 15:00"}]
                        },
                    }
                }
            }
        }
        result = _parse_timeline(raw)
        assert "wednesday" in result
        assert result["wednesday"]["checkin"] == "09:00 - 15:00"

    def test_absence_motivated_parsed(self):
        """Motivated absence is detected and stored in the day entry."""
        raw = _make_week({
            "data": [
                {
                    "id": "checkin",
                    "subtitle": "Absent",
                    "details": {
                        "presence": {
                            "temperature": None,
                            "absence": {
                                "reason": "vacation",
                                "motivated": True,
                                "info": "",
                                "by": "Parent Name",
                            },
                        }
                    },
                },
            ]
        })
        result = _parse_timeline(raw)
        monday = result["monday"]
        assert monday["checkin"] == "Absent"
        assert monday["absent"] is True
        assert monday["absence_reason"] == "vacation"
        assert monday["absence_motivated"] is True
        assert monday["absence_by"] == "Parent Name"

    def test_absence_unmotivated_parsed(self):
        """Unmotivated absence is also detected."""
        raw = _make_week({
            "data": [
                {
                    "id": "checkin",
                    "subtitle": "Absent",
                    "details": {
                        "presence": {
                            "temperature": None,
                            "absence": {
                                "reason": "sick",
                                "motivated": False,
                                "info": "",
                                "by": "",
                            },
                        }
                    },
                },
            ]
        })
        result = _parse_timeline(raw)
        monday = result["monday"]
        assert monday["absent"] is True
        assert monday["absence_motivated"] is False

    def test_no_absence_when_checked_in(self):
        """Normal check-in does not set the absent flag."""
        raw = _make_week({
            "data": [
                {
                    "id": "checkin",
                    "subtitle": "08:15 - by Parent Name",
                    "details": {
                        "presence": {
                            "temperature": None,
                            "absence": None,
                        }
                    },
                },
            ]
        })
        result = _parse_timeline(raw)
        assert "absent" not in result["monday"]

    def test_no_absence_without_details(self):
        """Checkin without details dict does not set the absent flag."""
        raw = _make_week({
            "data": [
                {"id": "checkin", "subtitle": "08:15 - 16:30"},
            ]
        })
        result = _parse_timeline(raw)
        assert "absent" not in result["monday"]
