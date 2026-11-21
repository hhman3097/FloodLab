"""Pass an authorized provider key to the UI test through stdin, never a file."""
import getpass
import subprocess
from pathlib import Path

key = getpass.getpass("API key for desktop live test (not stored): ")
subprocess.run(["node", "live_smoke.mjs"], input=key, text=True,
               cwd=Path(__file__).resolve().parent, check=True)
