"""
WebSocket manager for real-time updates.

Provides WebSocket endpoints for:
- Trade updates
- Price streaming
- Analysis updates
"""

import asyncio
import json
from typing import Dict, Set, Any
from datetime import datetime

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from ..utils.logging import get_logger

logger = get_logger(__name__)
router = APIRouter()


class ConnectionManager:
    """
    Manages WebSocket connections for real-time updates.
    
    Supports multiple channels:
    - trades: Trade open/close/update events
    - prices: Live price updates
    - analysis: ICT analysis updates
    """
    
    def __init__(self):
        # Channel -> Set of connections
        self.active_connections: Dict[str, Set[WebSocket]] = {
            "trades": set(),
            "prices": set(),
            "analysis": set(),
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
        
        # Clean up disconnected
        for conn in disconnected:
            self.active_connections[channel].discard(conn)
        
        # Also broadcast to "all" channel
        if channel != "all":
            for connection in self.active_connections["all"]:
                try:
                    await connection.send_text(message_json)
                except Exception:
                    self.active_connections["all"].discard(connection)
    
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


# Global connection manager
manager = ConnectionManager()


# Public functions for broadcasting from other modules
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


# WebSocket endpoints
@router.websocket("/trades")
async def websocket_trades(websocket: WebSocket):
    """
    WebSocket endpoint for trade updates.
    
    Clients receive:
    - New trade opened
    - Trade closed
    - Position updates
    """
    await manager.connect(websocket, "trades")
    
    try:
        # Send initial connection message
        await manager.send_personal(websocket, {
            "type": "connected",
            "channel": "trades",
            "message": "Connected to trade updates"
        })
        
        # Keep connection alive and handle incoming messages
        while True:
            try:
                data = await asyncio.wait_for(
                    websocket.receive_text(),
                    timeout=30.0
                )
                
                # Handle ping/pong for keepalive
                if data == "ping":
                    await manager.send_personal(websocket, {"type": "pong"})
                
            except asyncio.TimeoutError:
                # Send keepalive ping
                await manager.send_personal(websocket, {"type": "ping"})
                
    except WebSocketDisconnect:
        manager.disconnect(websocket, "trades")
    except Exception as e:
        logger.error(f"WebSocket error: {e}")
        manager.disconnect(websocket, "trades")


@router.websocket("/prices")
async def websocket_prices(websocket: WebSocket):
    """
    WebSocket endpoint for live price updates.
    
    Clients can subscribe to specific symbols by sending:
    {"action": "subscribe", "symbols": ["EURUSD", "GBPUSD"]}
    """
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
    """
    WebSocket endpoint for analysis updates.
    
    Receives updates when:
    - New ICT analysis is performed
    - Trade signals are generated
    - Market structure changes
    """
    await manager.connect(websocket, "analysis")
    
    try:
        await manager.send_personal(websocket, {
            "type": "connected",
            "channel": "analysis",
            "message": "Connected to analysis updates"
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
        manager.disconnect(websocket, "analysis")
    except Exception as e:
        logger.error(f"WebSocket error: {e}")
        manager.disconnect(websocket, "analysis")


@router.websocket("/all")
async def websocket_all(websocket: WebSocket):
    """
    WebSocket endpoint for all updates.
    
    Receives all trade, price, and analysis updates.
    """
    await manager.connect(websocket, "all")
    
    try:
        await manager.send_personal(websocket, {
            "type": "connected",
            "channel": "all",
            "message": "Connected to all updates"
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
        manager.disconnect(websocket, "all")
    except Exception as e:
        logger.error(f"WebSocket error: {e}")
        manager.disconnect(websocket, "all")


@router.get("/status")
async def websocket_status():
    """Get WebSocket connection statistics."""
    return {
        "connections": {
            "trades": manager.get_connection_count("trades"),
            "prices": manager.get_connection_count("prices"),
            "analysis": manager.get_connection_count("analysis"),
            "all": manager.get_connection_count("all")
        },
        "total": manager.get_connection_count()
    }
