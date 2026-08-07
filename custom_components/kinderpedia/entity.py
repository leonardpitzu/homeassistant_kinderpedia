"""Shared entity base for Kinderpedia."""

from __future__ import annotations

from datetime import timedelta

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .const import DOMAIN, MANUFACTURER
from .coordinator import KinderpediaDataUpdateCoordinator


class KinderpediaChildEntity(CoordinatorEntity[KinderpediaDataUpdateCoordinator]):
    """Base entity bound to a single child device."""

    def __init__(
        self,
        coordinator: KinderpediaDataUpdateCoordinator,
        child_id: int,
        kg_id: int,
        device_name: str,
    ) -> None:
        super().__init__(coordinator)
        self._key = f"{child_id}_{kg_id}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, self._key)},
            name=device_name,
            manufacturer=MANUFACTURER,
        )

    @property
    def _child_data(self) -> dict:
        """Return this child's slice of the coordinator payload."""
        data = self.coordinator.data or {}
        return data.get("children", {}).get(self._key, {})

    @property
    def _days(self) -> dict[str, dict]:
        """Return the current week's days, keyed by ISO date."""
        return self._child_data.get("days", {})

    @property
    def _last_updated(self) -> str | None:
        data = self.coordinator.data or {}
        return data.get("last_updated")

    def _week_days(self) -> dict[str, dict]:
        """Return this week's days keyed by weekday name (monday…sunday)."""
        today = dt_util.now().date()
        monday = today - timedelta(days=today.weekday())
        days = self._days
        return {
            day.get("name", ""): day
            for offset in range(7)
            if (day := days.get((monday + timedelta(days=offset)).isoformat()))
        }
