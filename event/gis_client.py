"""Subprocess transport shared by tests and the language-model controller."""
import json
import os
import subprocess
from pathlib import Path
from runtime import executable, environment

ROOT = Path(__file__).resolve().parent


class GISClient:
    def __init__(self, workspace, tracked=True, resume=False):
        self.workspace = Path(workspace).resolve()
        self.workspace.mkdir(parents=True, exist_ok=True)
        env = environment()
        args = [str(executable("python")), str(ROOT / "worker_bootstrap.py"),
                "--workspace", str(self.workspace)]
        if not tracked:
            args.append("--untracked")
        if resume:
            args.append("--resume")
        self.log = (self.workspace / "qgis-stderr.log").open("a" if resume else "w")
        self.process = subprocess.Popen(args, env=env, stdin=subprocess.PIPE,
                                        stdout=subprocess.PIPE, stderr=self.log, text=True)
        self.ready = json.loads(self.process.stdout.readline())
        if not self.ready.get("ready"):
            raise RuntimeError("QGIS worker did not initialize")

    def call(self, tool, **arguments):
        self.process.stdin.write(json.dumps({"tool": tool, "arguments": arguments}) + "\n")
        self.process.stdin.flush()
        line = self.process.stdout.readline()
        if not line:
            raise RuntimeError("QGIS worker exited; inspect qgis-stderr.log")
        return json.loads(line)

    def close(self):
        self.process.stdin.close()
        self.process.wait(timeout=30)
        self.log.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
