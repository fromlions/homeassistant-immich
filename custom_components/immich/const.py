"""Constants for the immich integration."""

DOMAIN = "immich"
CONF_WATCHED_ALBUMS = "watched_albums"
CONF_IMAGE_SIZE = "image_size"
CONF_REFRESH_INTERVAL = "refresh_interval"

SERVICE_NEXT_IMAGE = "next_image"

# Sizes of Immich's thumbnail endpoint that are fit for a dashboard: "preview" is about 1440 px, "fullsize"
# the whole picture converted to a web format. "original" is left out: it can be tens of megabytes (and HEIC or RAW).
IMAGE_SIZES = ["preview", "fullsize"]
DEFAULT_IMAGE_SIZE = "preview"

# Minutes between two new pictures.
DEFAULT_REFRESH_INTERVAL = 5
MIN_REFRESH_INTERVAL = 1
MAX_REFRESH_INTERVAL = 1440
