"""Worker for the local demo; replays reviewed outputs instead of calling any service."""

from backend.app.core.config import get_settings
from backend.app.worker import Worker, main
from backend.demo.providers import DemoProviders

if __name__ == "__main__":
    settings = get_settings()
    main(Worker(settings, providers=DemoProviders(settings)))
