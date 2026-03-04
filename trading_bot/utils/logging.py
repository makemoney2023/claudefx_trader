"""
Logging configuration for the trading bot.

Provides structured logging with both console and file output.
Includes log rotation for production use.
"""

import logging
import sys
from logging.handlers import RotatingFileHandler, TimedRotatingFileHandler
from pathlib import Path
from typing import Optional

import structlog

from ..config import settings


def setup_logging(log_level: Optional[str] = None) -> None:
    """
    Setup structured logging for the application.
    
    Args:
        log_level: Optional override for log level. Uses settings if not provided.
    """
    level = log_level or settings.logging.level
    numeric_level = getattr(logging, level.upper(), logging.INFO)
    
    # Create logs directory if needed
    if settings.logging.file_path:
        log_path = Path(settings.logging.file_path)
        log_path.parent.mkdir(parents=True, exist_ok=True)
    
    # Configure structlog
    structlog.configure(
        processors=[
            structlog.stdlib.filter_by_level,
            structlog.stdlib.add_logger_name,
            structlog.stdlib.add_log_level,
            structlog.stdlib.PositionalArgumentsFormatter(),
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.UnicodeDecoder(),
            structlog.processors.JSONRenderer() if settings.logging.file_path else structlog.dev.ConsoleRenderer(colors=True),
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )
    
    # Configure standard logging
    # Ensure stdout can handle Unicode on Windows
    if sys.platform == 'win32' and hasattr(sys.stdout, 'reconfigure'):
        try:
            sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        except Exception:
            pass
    
    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(logging.Formatter(settings.logging.format))
    handlers = [stream_handler]
    
    if settings.logging.file_path:
        rot_handler = RotatingFileHandler(
            settings.logging.file_path,
            maxBytes=10 * 1024 * 1024,
            backupCount=7,
            encoding='utf-8',
        )
        rot_handler.setFormatter(logging.Formatter(settings.logging.format))
        handlers.append(rot_handler)
    
    logging.basicConfig(
        format=settings.logging.format,
        level=numeric_level,
        handlers=handlers,
    )
    
    # Set third-party loggers to WARNING to reduce noise
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("anthropic").setLevel(logging.WARNING)


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    """
    Get a structured logger instance.
    
    Args:
        name: Name for the logger (usually __name__)
        
    Returns:
        Configured structlog logger
    """
    return structlog.get_logger(name)
