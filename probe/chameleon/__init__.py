"""Vendored Chameleon Ultra CLI engine (protocol + command layer only).

Source: RfidResearchGroup/ChameleonUltra, software/script/
        (chameleon_com.py, chameleon_cmd.py, chameleon_enum.py, chameleon_utils.py)
License: GNU General Public License v3.0 (see the repo root LICENSE).

Only the transport (ChameleonCom), the command layer (ChameleonCMD) and the wire
enums are vendored here; the interactive CLI (chameleon_cli_*, prompt_toolkit
completers) is NOT. chameleon_d.py drives these classes.

Local vendoring changes, limited to making the four files import as a package
with no third-party dependency at import time (the daemon must import on a bare
interpreter, exactly like x7d.py):
  - intra-package imports made relative (from .chameleon_enum import ...);
  - serial imported lazily inside ChameleonCom.open (only needed to open a port);
  - colorama / prompt_toolkit imports in chameleon_utils made optional (they are
    used only by the interactive CLI, never by ChameleonCom / ChameleonCMD).

One behavioural fix was applied to chameleon_com.ChameleonCom.send_cmd_sync: both
of its response-wait loops are now bounded and close-safe. Upstream spins forever
in `while cmd not in self.wait_response_map` and then KeyErrors in the next loop
if a transport thread calls close() (which clears wait_response_map) during that
window. The waits now use a deadline (timeout + 1s) and a .get() lookup, raising
TimeoutError (an OSError subclass, so the daemon's drop-and-reconnect path handles
it) instead of hanging or KeyErroring. GPL notices are kept intact.
"""
