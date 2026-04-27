import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "claude-usage-bot"))

import dotenv

# Load environment variables before importing slack module.
dotenv.load_dotenv()

from slack import start_bot  # noqa: E402


if __name__ == "__main__":
    start_bot()
