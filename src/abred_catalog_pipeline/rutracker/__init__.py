from . import parser as _parser
from .retry_client import RetryingRuTrackerWorkerClient

DEFAULT_AUDIOBOOK_FORUM_IDS = _parser.DEFAULT_AUDIOBOOK_FORUM_IDS
_parser.RuTrackerWorkerClient = RetryingRuTrackerWorkerClient
RuTrackerWorkerClient = RetryingRuTrackerWorkerClient

__all__ = ["DEFAULT_AUDIOBOOK_FORUM_IDS", "RuTrackerWorkerClient"]
