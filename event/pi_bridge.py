"""Run the bundled Pi agent. Only JSON tool results cross its private stdio."""
import asyncio
import json
import os
import shutil
from pathlib import Path
from karla_project import TOOL_NAMES as KARLA_TOOLS


async def _turn(api, system, context, cancel, search):
    binary=os.environ.get("FLOODLAB_PI_NODE") or shutil.which("node")
    if not binary:raise RuntimeError("Pi 运行环境缺失")
    env=dict(os.environ)
    if os.environ.get("FLOODLAB_PI_NODE"):env["ELECTRON_RUN_AS_NODE"]="1"
    child=await asyncio.create_subprocess_exec(binary,str(Path(__file__).with_name("pi-worker.cjs")),
        env=env,stdin=asyncio.subprocess.PIPE,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.DEVNULL,limit=2**20)
    async def write(obj):
        child.stdin.write((json.dumps(obj,ensure_ascii=False)+"\n").encode());await child.stdin.drain()
    try:
        await write({"key":api.key,"base":api.base,"model":api.model,"system":system,"context":context})
        deadline=asyncio.get_running_loop().time()+530
        while True:
            if cancel.is_set():raise InterruptedError("任务已停止")
            if asyncio.get_running_loop().time()>deadline:raise TimeoutError("Pi 请求超时")
            try:line=await asyncio.wait_for(child.stdout.readline(),timeout=.5)
            except asyncio.TimeoutError:continue
            if not line:raise RuntimeError("Pi 进程未返回结果")
            msg=json.loads(line)
            if "error" in msg:raise RuntimeError(str(msg["error"]).replace(api.key,"[REDACTED]"))
            if "final" in msg:return msg["final"]
            name=msg.get("tool")
            if name not in {"search_floods","find_events","list_products","search_satellite","get_footprint","analyse_footprint",
                            "sar_inventory","download_sar_pair","segment_sar_flood","publish_sar_map",
                            "transport_inventory","screen_transport","export_transport_map"} | KARLA_TOOLS:raise ValueError("Unknown Pi tool")
            try:result=await asyncio.to_thread(search,msg["args"],name)
            except (RuntimeError,ValueError,KeyError,OSError,TimeoutError) as exc:result={"error":str(exc)[:450]}
            await write(result)
    finally:
        if child.returncode is None:
            try:child.kill()
            except ProcessLookupError:pass
        await child.wait()


def turn(api,system,context,cancel,search):
    called=False
    def dispatch(args,name):
        nonlocal called
        called=True
        return search(args,name)
    try:
        return asyncio.run(_turn(api,system,context,cancel,dispatch))
    except RuntimeError as exc:
        if called or cancel.is_set() or not any(s in str(exc).lower() for s in ("connection error","econnreset","fetch failed")):
            raise
        return asyncio.run(_turn(api,system,context,cancel,dispatch))
