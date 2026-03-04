"""
WebSocket manager for real-time updates.

Provides WebSocket endpoints for:
- Trade updates
- Price streaming
- Analysis updates
- Activity feed (all bot events)
"""

import asyncio
import json
from typing import Dict, Set, Any, Optional
from datetime import datetime

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from ..utils.logging import get_logger
from .auth import get_api_key

logger = get_logger(__name__)
router = APIRouter()


class ConnectionManager:
    """
    Manages WebSocket connections for real-time updates.

    Supports multiple channels:
    - trades: Trade open/close/update events
    - prices: Live price updates
    - analysis: ICT analysis updates
    - activity: All bot activity events (25+ types)
    - all: Receives messages from every channel
    """

    def __init__(self):
        self.active_connections: Dict[str, Set[WebSocket]] = {
            "trades": set(),
            "prices": set(),
            "analysis": set(),
            "activity": set(),
            "all": set()
        }

    async def connect(self, websocket: WebSocket, channel: str = "all"):
        """Accept a new WebSocket connection."""
        await websocket.accept()

        if channel not in self.active_connections:
            self.active_connections[channel] = set()

        self.active_connections[channel].add(websocket)
        logger.info(f"WebSocket connected to channel: {channel}")

    def disconnect(self, websocket: WebSocket, channel: str = "all"):
        """Remove a WebSocket connection."""
        if channel in self.active_connections:
            self.active_connections[channel].discard(websocket)
        logger.info(f"WebSocket disconnected from channel: {channel}")

    async def broadcast(self, channel: str, message: dict):
        """Broadcast a message to all connections on a channel."""
        if channel not in self.active_connections:
            return

        message_json = json.dumps(message, default=str)
        disconnected = set()

        for connection in self.active_connections[channel]:
            try:
                await connection.send_text(message_json)
            except Exception:
                disconnected.add(connection)

        for conn in disconnected:
            self.active_connections[channel].discard(conn)

        if channel != "all":
            all_disconnected = set()
            for connection in self.active_connections["all"]:
                try:
                    await connection.send_text(message_json)
                except Exception:
                    all_disconnected.add(connection)
            for conn in all_disconnected:
                self.active_connections["all"].discard(conn)

    async def send_personal(self, websocket: WebSocket, message: dict):
        """Send a message to a specific connection."""
        try:
            await websocket.send_json(message)
        except Exception as e:
            logger.error(f"Error sending personal message: {e}")

    def get_connection_count(self, channel: str = None) -> int:
        """Get the number of active connections."""
        if channel:
            return len(self.active_connections.get(channel, set()))
        return sum(len(conns) for conns in self.active_connections.values())


manager = ConnectionManager()


async def _authenticate_websocket(websocket: WebSocket) -> bool:
    """Authenticate WebSocket connection via query parameter API key."""
    api_key = websocket.query_params.get("api_key")
    expected_key = get_api_key()

    if not expected_key:
        return True

    if not api_key or api_key != expected_key:
        await websocket.close(code=4001, reason="Invalid API key")
        return False
    return True


async def _run_channel_loop(websocket: WebSocket, channel: str):
    """Common keepalive loop for text-based channels."""
    if not await _authenticate_websocket(websocket):
        return

    await manager.connect(websocket, channel)

    try:
        await manager.send_personal(websocket, {
            "type": "connected",
            "channel": channel,
            "message": f"Connected to {channel} updates"
        })

        while True:
            try:
                data = await asyncio.wait_for(
                    websocket.receive_text(),
                    timeout=30.0
                )
                if data == "ping":
                    await manager.send_personal(websocket, {"type": "pong"})

            except asyncio.TimeoutError:
                await manager.send_personal(websocket, {"type": "ping"})

    except WebSocketDisconnect:
        manager.disconnect(websocket, channel)
    except Exception as e:
        logger.error(f"WebSocket error on {channel}: {e}")
        manager.disconnect(websocket, channel)


# --- Public broadcast functions ---

async def broadcast_trade_update(trade_data: dict):
    """Broadcast a trade update to all connected clients."""
    await manager.broadcast("trades", {
        "type": "trade_update",
        "timestamp": datetime.now().isoformat(),
        "data": trade_data
    })


async def broadcast_price_update(symbol: str, bid: float, ask: float):
    """Broadcast a price update to all connected clients."""
    await manager.broadcast("prices", {
        "type": "price_update",
        "timestamp": datetime.now().isoformat(),
        "data": {
            "symbol": symbol,
            "bid": bid,
            "ask": ask,
            "spread": ask - bid
        }
    })


async def broadcast_analysis_update(symbol: str, analysis_data: dict):
    """Broadcast an analysis update to all connected clients."""
    await manager.broadcast("analysis", {
        "type": "analysis_update",
        "timestamp": datetime.now().isoformat(),
        "data": {
            "symbol": symbol,
            **analysis_data
        }
    })


async def broadcast_activity(activity_data: dict):
    """Broadcast an activity event to all connected clients."""
    await manager.broadcast("activity", {
        "type": "activity",
        "timestamp": datetime.now().isoformat(),
        "data": activity_data
    })


# --- WebSocket endpoints ---

@router.websocket("/trades")
async def websocket_trades(websocket: WebSocket):
    """WebSocket endpoint for trade updates."""
    await _run_channel_loop(websocket, "trades")


@router.websocket("/prices")
async def websocket_prices(websocket: WebSocket):
    """
    WebSocket endpoint for live price updates.

    Clients can subscribe to specific symbols by sending:
    {"action": "subscribe", "symbols": ["EURUSD", "GBPUSD"]}
    """
    if not await _authenticate_websocket(websocket):
        return

    await manager.connect(websocket, "prices")
    subscribed_symbols: Set[str] = set()

    try:
        await manager.send_personal(websocket, {
            "type": "connected",
            "channel": "prices",
            "message": "Connected to price updates. Send subscribe message to receive prices."
        })

        while True:
            try:
                data = await asyncio.wait_for(
                    websocket.receive_json(),
                    timeout=30.0
                )

                action = data.get("action")

                if action == "subscribe":
                    symbols = data.get("symbols", [])
                    subscribed_symbols.update(s.upper() for s in symbols)
                    await manager.send_personal(websocket, {
                        "type": "subscribed",
                        "symbols": list(subscribed_symbols)
                    })

                elif action == "unsubscribe":
                    symbols = data.get("symbols", [])
                    subscribed_symbols -= set(s.upper() for s in symbols)
                    await manager.send_personal(websocket, {
                        "type": "unsubscribed",
                        "symbols": list(subscribed_symbols)
                    })

            except asyncio.TimeoutError:
                await manager.send_personal(websocket, {"type": "ping"})

    except WebSocketDisconnect:
        manager.disconnect(websocket, "prices")
    except Exception as e:
        logger.error(f"WebSocket error: {e}")
        manager.disconnect(websocket, "prices")


@router.websocket("/analysis")
async def websocket_analysis(websocket: WebSocket):
    """WebSocket endpoint for analysis updates."""
    await _run_channel_loop(websocket, "analysis")


@router.websocket("/activity")
async def websocket_activity(websocket: WebSocket):
    """WebSocket endpoint for all bot activity events."""
    await _run_channel_loop(websocket, "activity")


@router.websocket("/all")
async def websocket_all(websocket: WebSocket):
    """WebSocket endpoint for all updates across every channel."""
    await _run_channel_loop(websocket, "all")


@router.get("/status")
async def websocket_status():
    """Get WebSocket connection statistics."""
    return {
        "connections": {
            "trades": manager.get_connection_count("trades"),
            "prices": manager.get_connection_count("prices"),
            "analysis": manager.get_connection_count("analysis"),
            "activity": manager.get_connection_count("activity"),
            "all": manager.get_connection_count("all")
        },
        "total": manager.get_connection_count()
    }
