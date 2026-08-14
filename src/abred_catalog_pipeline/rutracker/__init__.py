from . import parser as _parser
from .cover import select_cover_from_post
from .retry_client import RetryingRuTrackerWorkerClient

DEFAULT_AUDIOBOOK_FORUM_IDS = _parser.DEFAULT_AUDIOBOOK_FORUM_IDS
_parser.RuTrackerWorkerClient = RetryingRuTrackerWorkerClient
_parser._cover_from_post = select_cover_from_post
RuTrackerWorkerClient = RetryingRuTrackerWorkerClient

__all__ = ["DEFAULT_AUDIOBOOK_FORUM_IDS", "RuTrackerWorkerClient"]
