"""
FastAPI backend for ICT Trading Bot Dashboard.

Provides REST API and WebSocket endpoints for:
- Trade monitoring and history
- ICT analysis data
- Bot configuration
- Real-time updates
"""

# Lazy imports to avoid circular dependency
def get_app():
    """Get the FastAPI app instance."""
    from .main import app
    return app

def get_create_app():
    """Get the create_app function."""
    from .main import create_app
    return create_app

__all__ = ["get_app", "get_create_app"]
