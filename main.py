# main.py
from fastapi import FastAPI
from pydantic import BaseModel
from agent_loop import run_agent

app = FastAPI(title="AI Office Assistant")

class ChatReq(BaseModel):
    task: str

@app.post("/chat")
def chat(req: ChatReq):
    return {"result": run_agent(req.task)}

# uvicorn main:app --reload