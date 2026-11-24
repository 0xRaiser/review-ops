#!/usr/bin/env python3
"""Open a new tab in the current iTerm2 window and type one command into it.

Usage: newtab.py "<command>"
"""
import sys

import iterm2


async def main(connection):
    app = await iterm2.async_get_app(connection)
    window = app.current_terminal_window
    if window is None:
        window = await iterm2.Window.async_create(connection)
        session = window.current_tab.current_session
    else:
        tab = await window.async_create_tab()
        session = tab.current_session
    await session.async_send_text(sys.argv[1] + "\n")
    print("opened tab, tty:", await session.async_get_variable("tty"))


iterm2.run_until_complete(main)
