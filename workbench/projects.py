"""Server-owned folder bookmarks; a chat gets its own child even in a shared parent."""
import json
import re
import uuid
from pathlib import Path


def save(path,obj):
    tmp=path.with_name(path.name+"."+uuid.uuid4().hex+".tmp")
    tmp.write_text(json.dumps(obj,ensure_ascii=False,indent=2),encoding="utf-8")
    tmp.replace(path)


class Projects:
    def __init__(self,runs):
        self.runs=Path(runs)
        self.book=self.runs/".folders"

    def bookmark(self,parent):
        p=Path(parent).resolve(strict=True)
        if not p.is_dir():raise ValueError("请选择工作区文件夹")
        self.book.mkdir(exist_ok=True)
        key=uuid.uuid4().hex
        save(self.book/key,{"parent":str(p)})
        return {"key":key,"name":p.name,"path":str(p)}

    def info(self,run):
        if not re.fullmatch(r"workbench_[0-9a-f]{10}",run):return {}
        p=self.runs/run/"project.json"
        return json.loads(p.read_text(encoding="utf-8")) if p.is_file() else {}

    def bind(self,run,source,key,title=""):
        inherited=self.info(source) if source else {}
        if inherited and key:raise ValueError("已有对话的工作区不能更换，请新建对话")
        if inherited:
            info=inherited
        else:
            if key:
                if not re.fullmatch(r"[0-9a-f]{32}",key) or not (self.book/key).is_file():raise ValueError("未知工作区")
                base=Path(json.loads((self.book/key).read_text(encoding="utf-8"))["parent"])
                if not base.is_dir():raise ValueError("工作区已移动或不可用")
            else:
                base=self.runs.parent/"projects";base.mkdir(exist_ok=True)
            root=base/("FloodLab-"+run[-10:]);root.mkdir()
            info={"id":run,"root":str(root),"name":base.name,"parent":str(base),"title":title.strip()[:44]}
            save(root/"workspace.json",info)
        root=Path(info["root"])
        if not root.is_dir() or root.is_symlink():raise ValueError("工作区已移动或不可用")
        directory=root/"turns"/run;directory.mkdir(parents=True)
        pointer=self.runs/run;pointer.mkdir()
        save(pointer/"project.json",info)
        return directory,info

    def directory(self,run):
        info=self.info(run)
        return Path(info["root"])/"turns"/run if info else self.runs/run
