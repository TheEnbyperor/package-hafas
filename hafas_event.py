import re
import hashlib
import pytz
from datetime import datetime, timedelta, tzinfo
from helper import Helper
from hosted import CONFIG
from mapping import CATEGORY_MAPPING, COLOUR_MAPPING, OPERATOR_LABEL_MAPPING

REMOVE = re.escape(CONFIG["remove_string"].strip()) if CONFIG["remove_string"] else None


class FixedOffset(tzinfo):
    def __init__(self, offset, name):
        self.__offset = timedelta(minutes=offset)
        self.__name = name

    def utcoffset(self, _dt):
        return self.__offset

    def tzname(self, _dt):
        return self.__name

    def dst(self, _dt):
        return timedelta(0)


class HAFASEvent:
    def __init__(self, data):
        self.json = data
        self.duplicate = False
        self.follow = None

        self.id = data["JourneyDetailRef"]["ref"]
        self.cancelled = data.get("cancelled", False)

        for product in data["Product"]:
            if product.get("name") and product.get("catCode"):
                self.category = product["catCode"]
                self.operator = product.get("operatorCode", None)
                self.operatorName = product.get("operatorInfo", {}).get("name", None)
                self.icon = product.get("icon", None)

                symbol = product["name"]
                for regex, replacement in OPERATOR_LABEL_MAPPING.get(
                        CONFIG["api_provider"], {}
                ).items():
                    symbol = re.sub(regex, replacement, symbol)
                self.symbol = symbol
                break
        else:
            self.symbol = ""
            self.category = -1
            self.operator = None
            self.operatorName = None
            self.icon = None

        if CONFIG["api_provider"] in CATEGORY_MAPPING:
            self.category_icon = CATEGORY_MAPPING[CONFIG["api_provider"]].get(
                str(self.category), ""
            )
        else:
            self.category_icon = ""

        self.scheduled = datetime.strptime(
            data["date"] + " " + data["time"], "%Y-%m-%d %H:%M:%S",
        )
        if data.get("tz", None) is not None:
            self.scheduled = self.scheduled.replace(tzinfo=FixedOffset(data["tz"], ""))
        else:
            self.scheduled = pytz.timezone(CONFIG["timezone"]).localize(self.scheduled)

        if "rtTime" in data and "rtDate" in data:
            self.realtime = datetime.strptime(
                data["rtDate"] + " " + data["rtTime"], "%Y-%m-%d %H:%M:%S"
            )
            if data.get("rtTz", None) is not None:
                self.realtime = self.realtime.replace(tzinfo=FixedOffset(data["rtTz"], ""))
            else:
                self.realtime = pytz.timezone(CONFIG["timezone"]).localize(self.realtime)
            diff = self.realtime - self.scheduled
            self.delay = int(diff.total_seconds() / 60)
        else:
            self.realtime = self.scheduled
            self.delay = None

    def __lt__(self, other):
        assert isinstance(other, HAFASEvent)
        return self.realtime < other.realtime

    def _clean(self, key):
        if key not in self.json:
            return None
        else:
            if REMOVE:
                for possible_match in (
                        "^(" + REMOVE + "[, -]+)",
                        "( *\(" + REMOVE + "\))",
                        "(" + REMOVE + " +)",
                ):
                    if re.search(possible_match, self.json[key].strip()):
                        return re.sub(
                            possible_match,
                            "",
                            self.json[key].strip(),
                            flags=re.IGNORECASE,
                        ).strip()
            return self.json[key].strip()

    @property
    def destination(self):
        return self._clean("direction")

    @property
    def origin(self):
        return self._clean("origin")

    @property
    def ignore_destination(self):
        if (
                CONFIG["ignore_destination"]
                and self.destination
                and re.search(
            CONFIG["ignore_destination"], self.destination, flags=re.IGNORECASE
        )
        ):
            return True
        return False

    @property
    def line_colour(self):
        provider = CONFIG["api_provider"]

        if (
                provider in COLOUR_MAPPING
                and self.operator in COLOUR_MAPPING[provider]
                and self.symbol in COLOUR_MAPPING[provider][self.operator]
        ):
            (r, g, b), (font_r, font_g, font_b) = COLOUR_MAPPING[provider][
                self.operator
            ][self.symbol]
        elif provider in COLOUR_MAPPING and self.symbol in COLOUR_MAPPING[provider]:
            (r, g, b), (font_r, font_g, font_b) = COLOUR_MAPPING[provider][self.symbol]
        elif provider in COLOUR_MAPPING and self.operator in COLOUR_MAPPING[provider]:
            (r, g, b), (font_r, font_g, font_b) = COLOUR_MAPPING[provider][self.operator]
        elif self.icon is not None:
            r, g, b = Helper.hex2rgb(self.icon["backgroundColor"]["hex"][1:])
            font_r, font_g, font_b = Helper.hex2rgb(
                self.icon["foregroundColor"]["hex"][1:]
            )
        else:
            name_hash = hashlib.md5(self.json["name"]).hexdigest()
            r, g, b = Helper.hex2rgb(name_hash[:6])
            h, s, v = Helper.rgb2hsv(r * 255, g * 255, b * 255)
            if v > 0.75:
                font_r, font_g, font_b = (0, 0, 0)
            else:
                font_r, font_g, font_b = (1, 1, 1)
        return {
            "background_colour": {
                "r": r,
                "g": g,
                "b": b,
            },
            "font_colour": {
                "r": font_r,
                "g": font_g,
                "b": font_b,
            },
        }

    @property
    def notes(self):
        notes = []
        if "Notes" in self.json:
            for note in self.json["Notes"]["Note"]:
                note_type = note["type"].upper()
                # Apparently:
                # A: Accessibility Information
                # I: Internal Stuff
                # R: Travel information ("faellt aus" etc.)
                # P: Cancellation reason
                # D: Late running reason
                if note_type in ("R", "P", "D"):
                    notes.append({
                        "type": note_type,
                        "text": note["value"]
                    })
        return notes

    @property
    def stop(self):
        return self._clean("stop")

    @property
    def platform(self):
        if "rtPlatform" in self.json or "platform" in self.json:
            platform = self.json["rtPlatform"] if "rtPlatform" in self.json else self.json["platform"]
            if platform.get("hidden", False):
                return None
            return {
                "type": platform.get("type", "X"),
                "value": platform.get("text", ""),
            }
        if "track" in self.json:
            return {
                "type": "PL",
                "value": self.json["track"]
            }
        return None
