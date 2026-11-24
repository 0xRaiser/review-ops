#!/usr/bin/env python3
"""Bring the iTerm2 tab whose terminal device matches the given tty to the front.

Usage: focus.py ttys004      (or /dev/ttys004)
       focus.py --list       (print tty and title of every session)
"""
import sys

import iterm2


async def main(connection):
    app = await iterm2.async_get_app(connection)
    want = sys.argv[1] if len(sys.argv) > 1 else "--list"
    if want != "--list" and not want.startswith("/dev/"):
        want = "/dev/" + want
    for window in app.terminal_windows:
        for tab in window.tabs:
            for session in tab.sessions:
                tty = await session.async_get_variable("tty")
                if want == "--list":
                    print(tty, session.name)
                elif tty == want:
                    await app.async_activate()
                    await window.async_activate()
                    await tab.async_activate()
                    await session.async_activate()
                    print("focused", tty, session.name)
                    return
    if want != "--list":
        print("not found", want)
        sys.exit(1)


iterm2.run_until_complete(main)
