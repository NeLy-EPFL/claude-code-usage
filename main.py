import sys
from pathlib import Path

# Add the package directory to path
sys.path.insert(0, str(Path(__file__).parent / "claude-usage-bot"))

import dotenv

# Load environment variables before importing slack module.
dotenv.load_dotenv()

# Import from the package directory
from slack import start_bot  # noqa: E402


def main():
    # Start the Slack bot
    start_bot()


if __name__ == "__main__":
    main()
