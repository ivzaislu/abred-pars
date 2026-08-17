from __future__ import annotations

import logging

# Telegram Bot API tokens are embedded in request URLs. Keep HTTP client
# request logging above INFO so tokens cannot be written to application logs.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

from .telegram_bot import main


if __name__ == "__main__":
    main()
