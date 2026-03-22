import asyncio
import json
import os
import sys

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

router = APIRouter()

_IS_WINDOWS = sys.platform == "win32"

if _IS_WINDOWS:
    from winpty import PtyProcess
else:
    from ptyprocess import PtyProcess


async def _read_pty(pty: PtyProcess, websocket: WebSocket) -> None:
    """Continuously read from the PTY and send output to the WebSocket."""
    loop = asyncio.get_event_loop()
    while True:
        try:
            # Non-blocking read via executor; ptyprocess.read() blocks
            data = await loop.run_in_executor(None, _safe_read, pty)
            if data is None:
                # PTY has closed
                break
            await websocket.send_bytes(data)
        except WebSocketDisconnect:
            break
        except Exception:
            break


def _safe_read(pty: PtyProcess) -> bytes | None:
    """Read from PTY; return None on EOF/error.
    pywinpty returns str; ptyprocess returns bytes — normalise to bytes."""
    try:
        data = pty.read(4096)
        if isinstance(data, str):
            return data.encode("utf-8", errors="replace")
        return data
    except EOFError:
        return None
    except Exception:
        return None


@router.websocket("/ws/terminal")
async def terminal_ws(websocket: WebSocket) -> None:
    """
    WebSocket PTY terminal endpoint.

    Client → Server:
        JSON {"type": "input",  "data": "..."}       — keyboard input
        JSON {"type": "resize", "cols": N, "rows": N} — terminal resize

    Server → Client:
        raw bytes — terminal output
    """
    await websocket.accept()

    home_dir = os.path.expanduser("~")
    pty: PtyProcess | None = None

    try:
        if _IS_WINDOWS:
            shell = os.environ.get("COMSPEC", "powershell.exe")
            pty = PtyProcess.spawn(shell, cwd=home_dir)
        else:
            shell = os.environ.get("SHELL", "/bin/zsh")
            pty = PtyProcess.spawn(
                [shell],
                cwd=home_dir,
                env={**os.environ, "TERM": "xterm-256color"},
            )

        # Start background reader task
        read_task = asyncio.create_task(_read_pty(pty, websocket))

        try:
            while True:
                message = await websocket.receive_text()
                try:
                    msg = json.loads(message)
                except json.JSONDecodeError:
                    continue

                msg_type = msg.get("type")

                if msg_type == "input":
                    data = msg.get("data", "")
                    if data and pty.isalive():
                        # pywinpty expects str; ptyprocess expects bytes
                        pty.write(data if _IS_WINDOWS else data.encode("utf-8", errors="replace"))

                elif msg_type == "resize":
                    cols = int(msg.get("cols", 80))
                    rows = int(msg.get("rows", 24))
                    if pty.isalive():
                        pty.setwinsize(rows, cols)

        except WebSocketDisconnect:
            pass
        finally:
            read_task.cancel()
            try:
                await read_task
            except asyncio.CancelledError:
                pass

    except Exception as e:
        print(f"[terminal] Error: {e}")
    finally:
        if pty is not None:
            try:
                pty.terminate(force=True)
            except Exception:
                pass
