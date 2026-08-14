from . import parser as _parser
from .cover import select_cover_from_post
from .retry_client import RetryingRuTrackerWorkerClient
from .series import normalize_series_name

DEFAULT_AUDIOBOOK_FORUM_IDS = _parser.DEFAULT_AUDIOBOOK_FORUM_IDS
_parser.RuTrackerWorkerClient = RetryingRuTrackerWorkerClient
_parser._cover_from_post = select_cover_from_post
_parser._normalize_series_name = normalize_series_name
RuTrackerWorkerClient = RetryingRuTrackerWorkerClient

__all__ = ["DEFAULT_AUDIOBOOK_FORUM_IDS", "RuTrackerWorkerClient"]
