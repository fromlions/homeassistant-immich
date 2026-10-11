"""The entry's options, with their defaults, read in one place."""
from __future__ import annotations

from collections.abc import Mapping
from datetime import timedelta
from typing import Any

from .const import (
    CONF_IMAGE_SIZE,
    CONF_REFRESH_INTERVAL,
    DEFAULT_IMAGE_SIZE,
    DEFAULT_REFRESH_INTERVAL,
    IMAGE_SIZES,
    MAX_REFRESH_INTERVAL,
    MIN_REFRESH_INTERVAL,
)


def image_size(options: Mapping[str, Any]) -> str:
    """The thumbnail size to download; the default when the option is not set or not one Immich offers."""
    size = options.get(CONF_IMAGE_SIZE, DEFAULT_IMAGE_SIZE)
    return size if size in IMAGE_SIZES else DEFAULT_IMAGE_SIZE


def refresh_minutes(options: Mapping[str, Any]) -> int:
    """Minutes between two new pictures, kept within the allowed range."""
    try:
        minutes = int(options.get(CONF_REFRESH_INTERVAL, DEFAULT_REFRESH_INTERVAL))
    except (TypeError, ValueError):
        return DEFAULT_REFRESH_INTERVAL
    return min(max(minutes, MIN_REFRESH_INTERVAL), MAX_REFRESH_INTERVAL)


def refresh_interval(options: Mapping[str, Any]) -> timedelta:
    """The refresh minutes as a timedelta."""
    return timedelta(minutes=refresh_minutes(options))
