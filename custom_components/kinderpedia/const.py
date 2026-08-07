DOMAIN = "kinderpedia"

LOGIN_URL = "https://usergateway-services.kinderpedia.co/api/login"
CORE_URL = "https://app.kinderpedia.co/web-api/data/parent-app-core"
DATA_URL = "https://app.kinderpedia.co/web-api/data/dailytimeline?week={week}"
NEWSFEED_URL = "https://app.kinderpedia.co/web-api/data/newsfeed"

API_KEY = "Web01Pari3l4em|v1.02"

PLATFORMS = ["sensor", "calendar"]

MANUFACTURER = "Kinderpedia"

# Seconds before an HTTP request is aborted.
REQUEST_TIMEOUT = 30

# How often the coordinator polls the API.
UPDATE_INTERVAL_MINUTES = 15

SCHOOL_WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday"]
WEEKDAY_NAMES = [*SCHOOL_WEEKDAYS, "saturday", "sunday"]
