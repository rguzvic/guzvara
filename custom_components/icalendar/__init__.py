"""Export calendar domain entity state via iCalendar using the API."""

import logging

from html import escape
from http import HTTPStatus

from aiohttp import web
import datetime

from homeassistant.components.http import HomeAssistantView
from homeassistant.core import HomeAssistant
from homeassistant.const import (
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
)
from homeassistant.helpers.typing import ConfigType

from .const import DOMAIN, CONTENT_TYPE_ICAL


_LOGGER = logging.getLogger(__name__)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up the iCalendar component."""
    for name, value in config[DOMAIN].items():
        # Find the secret from the config file
        if name == "secret":
            secret = str(value)

            # Register the iCalendar HTTP view
            hass.http.register_view(iCalendarView(hass, secret))
            return True

    return False


class iCalendarView(HomeAssistantView):
    """Define the iCalendar view."""

    name = f"{DOMAIN}"
    url = "/api/ics/{entity_id}"

    def __init__(self, hass: HomeAssistant, secret: str) -> None:
        """Initialize the iCalendar view."""
        self.hass = hass
        self.secret = secret
        self.requires_auth = False

    async def get(self, request: web.Request, entity_id: str) -> web.Response:
        """Handle an iCalendar view request."""
        # Forbid empty secrets
        if request.query.get("s") is None:
            _LOGGER.error("Request was sent for entity '%s' without secret", entity_id)
            return web.Response(status=HTTPStatus.FORBIDDEN)

        # Only return anything with the secret supplied
        if str(request.query.get("s")) != str(self.secret):
            _LOGGER.error(
                "Request was sent for entity '%s' with invalid secret", entity_id
            )
            return web.Response(status=HTTPStatus.UNAUTHORIZED)

        # Only return calendars
        if not entity_id.startswith("calendar."):
            _LOGGER.error("Entity '%s' is not a calendar", entity_id)
            return web.Response(status=HTTPStatus.FORBIDDEN)

        # Get the calendar entity state
        self._state = self.hass.states.get(entity_id)

        # Check if the calendar entity exists
        if self._state is None:
            _LOGGER.error("Entity '%s' could not be found", entity_id)
            return web.Response(status=HTTPStatus.NOT_FOUND)

        # Check if the calendar entity is available
        if self._state in (STATE_UNKNOWN, STATE_UNAVAILABLE):
            _LOGGER.error("Entity '%s' could not be found", entity_id)
            return web.Response(status=HTTPStatus.SERVICE_UNAVAILABLE)

        # Calculate the start and end timeframe for our calendar
        # We output 4 weeks history and 52 weeks into the future
        start = (datetime.datetime.now() - datetime.timedelta(weeks=4)).strftime("%Y-%m-%d %H:%M:%S")
        end = (datetime.datetime.now() + datetime.timedelta(weeks=52)).strftime("%Y-%m-%d %H:%M:%S")

        events = await self.hass.services.async_call('calendar', 'get_events',
              { "entity_id": entity_id,
                "start_date_time": start,
                "end_date_time": end
              }, blocking=True, return_response=True)

        if(events is None) or (entity_id not in events):
            _LOGGER.error("Entity '%s' has no events", entity_id)
            return web.Response(body="404: Not Found", status=HTTPStatus.NOT_FOUND)

        events = events[entity_id]['events']

        # Craft the iCalendar response
        response = "BEGIN:VCALENDAR\n"
        response += "VERSION:2.0\n"
        response += "PRODID:-//Home Assistant//iCal Subscription 1.0//EN\n"
        response += "CALSCALE:GREGORIAN\n"
        response += "METHOD:PUBLISH\n"
        response += f"ORGANIZER;CN=\"{escape(self._state.attributes['friendly_name'])}\":MAILTO:{entity_id}@homeassistant.local\n"
        response += f"NAME:{escape(self._state.attributes['friendly_name'])}\n"

        # Generate the variables
        entity_id = escape(entity_id)

        # Iterate through all the events
        for e in events:
            try:
                start = datetime.datetime.strptime(
                    e["start"], "%Y-%m-%dT%H:%M:%S%z"
                ).strftime("%Y%m%dT%H%M%S")
                end = datetime.datetime.strptime(
                    e["end"], "%Y-%m-%dT%H:%M:%S%z"
                ).strftime("%Y%m%dT%H%M%S")
                dtstamp = start
            except:
                start = datetime.datetime.strptime(
                    e["start"], "%Y-%m-%d"
                ).strftime("%Y%m%d")
                end = datetime.datetime.strptime(
                    e["end"], "%Y-%m-%d"
                ).strftime("%Y%m%d")
                dtstamp = f'{start}T000000'
            uid = f"{entity_id}-{start}"

            response += "BEGIN:VEVENT\n"

            response += f"UID:{uid}\n"
            response += f"DTSTAMP:{dtstamp}\n"
            response += f"DTSTART:{start}\n"
            response += f"DTEND:{end}\n"

            # Add available optional attribuets to the iCalendar response
            if (
                "summary" in e
                and e["summary"] is not None
            ):
                response += f"SUMMARY:{escape(e['summary']).replace('\n', '\n ').rstrip()}\n"

            if (
                "description" in e
                and e["description"] is not None
            ):
                response += (
                    f"DESCRIPTION:{escape(e['description']).replace('\n', '\n ').rstrip()}\n"
                )

            if (
                "location" in e
                and e["location"] is not None
            ):
                response += f"LOCATION:{escape(e['location']).replace('\n', '\n ').rstrip()}\n"

            # Finish up this calendar entry
            response += "END:VEVENT\n"

        # Finish up the iCalendar response
        response += "END:VCALENDAR"

        # Return the iCalendar response
        return web.Response(body=response, content_type=CONTENT_TYPE_ICAL)
