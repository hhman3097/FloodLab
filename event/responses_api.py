"""Responses transport; preserve the original GIS experiment's local transcript schema."""
from gis_agent import API as BaseAPI, TOOLS


class API(BaseAPI):
    def __init__(self, base, key, model):
        super().__init__(base,key,model)
        self.reasoning={}

    def complete(self,messages):
        items=[]
        for m in messages:
            role=m["role"]
            if role=="tool":
                items.append({"type":"function_call_output","call_id":m["tool_call_id"],"output":m["content"]})
                continue
            content=m.get("content")
            if isinstance(content,list):
                content=[{"type":"input_image","image_url":x["image_url"]["url"]} if x["type"]=="image_url"
                         else {"type":"input_text","text":x["text"]} for x in content]
            calls=m.get("tool_calls",[])
            if calls:items.extend(self.reasoning.get(calls[0]["id"],[]))
            if content:items.append({"role":role,"content":content})
            for c in calls:items.append({"type":"function_call","call_id":c["id"],**c["function"]})
        result=self.request("/responses",{"model":self.model,"input":items,"store":False,
             "tools":[{"type":"function",**t["function"],"strict":False} for t in TOOLS],
             "parallel_tool_calls":False,"include":["reasoning.encrypted_content"],"max_output_tokens":4000})
        if result.get("status") in ("failed","incomplete","cancelled"):
            raise RuntimeError("Responses request "+str(result["status"])+": "+str(result.get("incomplete_details") or result.get("error")))
        calls=[];parts=[];reasoning=[]
        for item in result.get("output",[]):
            if item["type"]=="function_call":
                calls.append({"id":item["call_id"],"type":"function","function":{"name":item["name"],"arguments":item["arguments"]}})
            elif item["type"]=="message":
                parts.extend(p.get("text") or p.get("refusal","") for p in item.get("content",[]))
            elif item["type"]=="reasoning" and item.get("encrypted_content"):
                reasoning.append({k:item[k] for k in ("type","id","summary","encrypted_content") if k in item})
        if calls:self.reasoning[calls[0]["id"]]=reasoning
        if not calls and not any(parts):raise RuntimeError("Responses API returned no text or tool calls")
        use=result.get("usage") or {}
        return {"model":result.get("model"),"usage":{"prompt_tokens":use.get("input_tokens",0),
            "completion_tokens":use.get("output_tokens",0),"total_tokens":use.get("total_tokens",0)},
            "choices":[{"finish_reason":"tool_calls" if calls else "stop","message":{"role":"assistant",
                       "content":"".join(parts) or None,**({"tool_calls":calls} if calls else {})}}]}
