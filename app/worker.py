"""Background workers: run Telegram work on its own asyncio loop in a QThread.

The GUI thread never blocks. Login prompts (SMS code, 2FA password) are
bridged to the GUI via `sig_ask` — the worker thread blocks on a
threading.Event until the dialog answers via `provide_answer()`. That bridge
is shared by two workers: `ToolWorker` (phone + code login, then runs a tool)
and `QrLoginWorker` (QR-code login, used once from the Config tab to
authorize the session so later tool runs need no phone/code at all).
"""
from __future__ import annotations

import asyncio
import threading
import traceback

from PySide6.QtCore import QThread, Signal


class Ctx:
    """Callbacks handed to tool coroutines."""

    def __init__(self, worker: "ToolWorker") -> None:
        self._w = worker
        # Keys of items a tool has fully finished (see item_done). Lives as
        # long as the worker, so when the job switches Telegram accounts
        # mid-way (ToolWorker._main) and the tool is re-run, it can skip them.
        self.done: set = set()

    def item_done(self, key) -> None:
        self.done.add(key)

    def can_switch(self) -> bool:
        """True while this job still has a second Telegram account to fall
        back on (a long FloodWait then raises common.AccountLimited)."""
        return self._w.alt_conn is not None

    def log(self, msg: str) -> None:
        self._w.sig_log.emit(str(msg))

    def progress(self, done: int, total: int) -> None:
        # total == 0 -> indeterminate
        self._w.sig_progress.emit(int(done), int(total))

    def cancelled(self) -> bool:
        return self._w.cancel_requested


class _AskThread(QThread):
    """QThread with the code/password prompt bridge shared by both login flows."""

    sig_ask = Signal(str, str)  # kind ('code'|'password'), prompt

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.cancel_requested = False
        self._answer: str | None = None
        self._evt = threading.Event()

    # ------------------------------------------------- GUI-thread methods
    def request_cancel(self) -> None:
        self.cancel_requested = True
        self._evt.set()  # unblock a pending login prompt, if any

    def provide_answer(self, value: str) -> None:
        self._answer = value
        self._evt.set()

    # ----------------------------------------------- worker-thread methods
    def _ask(self, kind: str, prompt: str) -> str:
        self._evt.clear()
        self._answer = None
        self.sig_ask.emit(kind, prompt)
        self._evt.wait()
        if self.cancel_requested or self._answer is None:
            raise RuntimeError("Login cancelled")
        return self._answer


class ToolWorker(_AskThread):
    sig_log = Signal(str)
    sig_progress = Signal(int, int)         # done, total (0 = unknown)
    sig_done = Signal(bool, str)            # ok, message

    def __init__(self, tool_func, params: dict, conn: dict, parent=None,
                 alt_conn: dict | None = None) -> None:
        """conn: {'api_id','api_hash','phone','session'}. `alt_conn`, same
        shape (Config.alt_conn): the other account — if the running one hits
        a long FloodWait, the job carries on there instead of sleeping it out
        (at most once per job)."""
        super().__init__(parent)
        self.tool_func = tool_func
        self.params = params
        self.conn = conn
        self.alt_conn = alt_conn

    def run(self) -> None:  # QThread entry
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            result = loop.run_until_complete(self._main())
            self.sig_done.emit(not self.cancel_requested, result or "")
        except Exception as exc:  # noqa: BLE001 - surfaced to the GUI log
            self.sig_log.emit(traceback.format_exc())
            self.sig_done.emit(False, str(exc))
        finally:
            try:
                loop.run_until_complete(loop.shutdown_asyncgens())
            except Exception:
                pass
            loop.close()

    async def _connect(self, ctx):
        from telethon import TelegramClient

        client = TelegramClient(
            self.conn["session"], int(self.conn["api_id"]), self.conn["api_hash"]
        )
        ctx.log("Connecting to Telegram…")
        await client.start(
            phone=lambda: self.conn["phone"],
            code_callback=lambda: self._ask("code", "code"),
            password=lambda: self._ask("password", "password"),
        )
        me = await client.get_me()
        ctx.log(f"Connected as {getattr(me, 'first_name', '')} "
                f"(+{getattr(me, 'phone', '?')}).")
        return client

    async def _main(self) -> str:
        from telethon import errors

        from .tools.common import FLOOD_SWITCH_SECONDS, AccountLimited

        ctx = Ctx(self)
        client = await self._connect(ctx)
        try:
            while True:
                try:
                    return await self.tool_func(client, self.params, ctx)
                except AccountLimited as lim:
                    seconds = lim.seconds
                except errors.FloodWaitError as exc:
                    # Escaped a tool that doesn't catch per item (a single
                    # channel fetch). Only worth switching for a long wait and
                    # only if there's somewhere to switch to.
                    seconds = int(getattr(exc, "seconds", 0) or 0)
                    if self.alt_conn is None or seconds < FLOOD_SWITCH_SECONDS:
                        raise
                # Long FloodWait on this account: hand the rest of the job to
                # the other one. Once — the other account can be limited too,
                # and bouncing between two limited accounts helps nobody.
                ctx.log(f"FloodWait: Telegram asks this account to wait "
                        f"{seconds}s — switching to the other account.")
                await client.disconnect()
                self.conn, self.alt_conn = self.alt_conn, None
                client = await self._connect(ctx)
        finally:
            await client.disconnect()


class CheckLoginWorker(QThread):
    """Connects with the stored session and reports whether it's authorized."""

    sig_done = Signal(bool, str, str)  # authorized, name, phone (or error in name)

    def __init__(self, api_id: str, api_hash: str, session: str, parent=None) -> None:
        super().__init__(parent)
        self.api_id = api_id
        self.api_hash = api_hash
        self.session = session

    def run(self) -> None:  # QThread entry
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            ok, name, phone = loop.run_until_complete(self._main())
            self.sig_done.emit(ok, name, phone)
        except Exception as exc:  # noqa: BLE001 - surfaced to the GUI
            self.sig_done.emit(False, str(exc), "")
        finally:
            try:
                loop.run_until_complete(loop.shutdown_asyncgens())
            except Exception:
                pass
            loop.close()

    async def _main(self) -> tuple[bool, str, str]:
        from telethon import TelegramClient

        client = TelegramClient(self.session, int(self.api_id), self.api_hash)
        await client.connect()
        try:
            if not await client.is_user_authorized():
                return False, "", ""
            me = await client.get_me()
            return True, str(getattr(me, "first_name", "") or ""), str(getattr(me, "phone", "?"))
        finally:
            await client.disconnect()


class QrLoginWorker(_AskThread):
    """Authorizes a session by QR code — no phone number required.

    Telegram's QR token expires after a short while, so the flow keeps
    regenerating it (`sig_qr`) until the user scans it or cancels. If the
    account has 2FA enabled, it falls back to the same password prompt
    `ToolWorker` uses.
    """

    sig_qr = Signal(str)          # tg://login?token=... URL to render as a QR code
    sig_status = Signal(str)      # 'waiting' | 'expired'
    sig_done = Signal(bool, str)  # ok, message

    def __init__(self, api_id: str, api_hash: str, session: str, parent=None) -> None:
        super().__init__(parent)
        self.api_id = api_id
        self.api_hash = api_hash
        self.session = session

    def run(self) -> None:  # QThread entry
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            result = loop.run_until_complete(self._main())
            self.sig_done.emit(not self.cancel_requested, result or "")
        except Exception as exc:  # noqa: BLE001 - surfaced to the GUI
            self.sig_done.emit(False, str(exc))
        finally:
            try:
                loop.run_until_complete(loop.shutdown_asyncgens())
            except Exception:
                pass
            loop.close()

    async def _main(self) -> str:
        from telethon import TelegramClient
        from telethon.errors import SessionPasswordNeededError

        client = TelegramClient(self.session, int(self.api_id), self.api_hash)
        await client.connect()
        try:
            if await client.is_user_authorized():
                return "Already logged in"

            qr = None
            while not self.cancel_requested:
                try:
                    # Both (re)creating the token and waiting on it issue
                    # ExportLoginTokenRequest under the hood, and either call
                    # can be the one that reports 2FA is required — so both
                    # need to be inside the same password try/except.
                    if qr is None:
                        qr = await client.qr_login()
                    else:
                        await qr.recreate()
                    self.sig_qr.emit(qr.url)
                    self.sig_status.emit("waiting")
                    await qr.wait()
                    break
                except asyncio.TimeoutError:
                    self.sig_status.emit("expired")
                except SessionPasswordNeededError:
                    password = self._ask("password", "password")
                    await client.sign_in(password=password)
                    break

            if self.cancel_requested:
                return "Cancelled"

            me = await client.get_me()
            return (f"Logged in as {getattr(me, 'first_name', '')} "
                    f"(+{getattr(me, 'phone', '?')}).")
        finally:
            await client.disconnect()
