import asyncio
import websockets
import json
import logging
import random
import os

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

ROOMS = {}
WINNING_CONDITIONS = [
    [0, 1, 2], [3, 4, 5], [6, 7, 8],
    [0, 3, 6], [1, 4, 7], [2, 5, 8],
    [0, 4, 8], [2, 4, 6]
]

def generate_code():
    """Generate a unique room code"""
    available = [str(i) for i in range(101) if str(i) not in ROOMS]
    return random.choice(available) if available else None

async def send(ws, msg_type, data=None):
    """Send message to websocket"""
    try:
        msg = {"type": msg_type, "data": data or {}}
        await ws.send(json.dumps(msg))
    except websockets.ConnectionClosed:
        pass

async def broadcast(code, msg_type, data=None):
    """Send message to all players in room"""
    if code not in ROOMS:
        return
    room = ROOMS[code]
    targets = [room.get("player_x"), room.get("player_o")]
    await asyncio.gather(*[send(ws, msg_type, data) for ws in targets if ws])

def find_room(ws):
    """Find which room a player is in"""
    for code, room in ROOMS.items():
        if room.get("player_x") == ws or room.get("player_o") == ws:
            return code, room
    return None, None

def check_win(board):
    """Check for winner or tie"""
    for a, b, c in WINNING_CONDITIONS:
        if board[a] and board[a] == board[b] == board[c]:
            return board[a], [a, b, c]
    return ("TIE", None) if "" not in board else (None, None)

async def handle_create(ws):
    """Create new room (Player X)"""
    code = generate_code()
    if not code:
        await send(ws, "error", {"message": "Server full"})
        return
    
    ROOMS[code] = {
        "player_x": ws,
        "player_o": None,
        "board": [""] * 9,
        "turn": "X"
    }
    logger.info(f"Room {code} created")
    await send(ws, "room_created", {"code": code})

async def handle_join(ws, code):
    """Join existing room (Player O)"""
    if code not in ROOMS:
        await send(ws, "error", {"message": "Room not found"})
        return
    
    room = ROOMS[code]
    if room["player_o"]:
        await send(ws, "error", {"message": "Room full"})
        return
    
    room["player_o"] = ws
    logger.info(f"Player joined room {code}")
    
    await send(room["player_x"], "opponent_joined", {"code": code})
    await send(ws, "room_joined", {"code": code})

async def handle_move(ws, code, index, player):
    """Process game move"""
    if code not in ROOMS:
        return
    
    room = ROOMS[code]
    
    if room["turn"] != player or room["board"][index] != "":
        await send(ws, "error", {"message": "Invalid move"})
        return
    
    room["board"][index] = player
    winner, win_line = check_win(room["board"])
    
    if winner:
        msg_type = "game_win" if winner != "TIE" else "game_tie"
        await broadcast(code, msg_type, {
            "winner": winner,
            "condition": win_line,
            "index": index,
            "player": player
        })
        room["board"] = [""] * 9
        room["turn"] = "X"
    else:
        room["turn"] = "O" if player == "X" else "X"
        await broadcast(code, "game_move", {"index": index, "player": player})
        await broadcast(code, "turn_switch", {"player": room["turn"]})

async def unregister(ws):
    """Handle player disconnect"""
    code, room = find_room(ws)
    if not code:
        return
    
    symbol = "X" if room["player_x"] == ws else "O"
    logger.info(f"Player {symbol} disconnected from room {code}")
    
    if symbol == "X":
        room["player_x"] = None
    else:
        room["player_o"] = None
    
    if not room["player_x"] and not room["player_o"]:
        del ROOMS[code]
    else:
        await broadcast(code, "opponent_disconnected", {"disconnected": symbol})

async def handler(ws, path):
    """Main WebSocket handler"""
    logger.info(f"Client connected: {ws.remote_address}")
    
    try:
        async for message in ws:
            try:
                data = json.loads(message)
                msg_type = data.get("type")
                msg_data = data.get("data", {})
                
                if msg_type == "create_room":
                    await handle_create(ws)
                elif msg_type == "join_room":
                    await handle_join(ws, msg_data.get("code"))
                elif msg_type == "game_move":
                    await handle_move(
                        ws,
                        msg_data.get("code"),
                        msg_data.get("index"),
                        msg_data.get("player")
                    )
            except json.JSONDecodeError:
                await send(ws, "error", {"message": "Invalid JSON"})
    except websockets.ConnectionClosed:
        pass
    finally:
        await unregister(ws)

async def main():
    port = int(os.environ.get("PORT", 8000))
    logger.info(f"Starting WebSocket server on port {port}")
    
    async with websockets.serve(handler, "0.0.0.0", port):
        logger.info(f"WebSocket server listening on 0.0.0.0:{port}")
        await asyncio.Future()

if __name__ == "__main__":
    asyncio.run(main())
