# api_server.py - FastAPI Server with MCP Integration

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from contextlib import asynccontextmanager
from mcpclient import MCPAIAssistant
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI()

# Add this middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"], # Allow Next.js frontend
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Global assistant
assistant = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage application lifespan"""
    global assistant
    try:
        logger.info(" Initializing MCP AI Assistant...")
        assistant = MCPAIAssistant()
        await assistant.initialize()
        logger.info(" MCP AI Assistant ready!")
        yield
    except Exception as e:
        logger.error(f" Failed to initialize: {e}")
        logger.error("   Make sure mcp_server.py is running first!")
        yield
    finally:
        logger.info(" Shutting down MCP AI Assistant...")
        if assistant:
            await assistant.close()
        logger.info(" Shutdown complete!")

app = FastAPI(title="MCP AI Assistant API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class ChatRequest(BaseModel):
    message: str

class ChatResponse(BaseModel):
    response: str
    status: str = "success"

@app.get("/")
async def root():
    return {
        "message": "MCP AI Assistant API",
        "status": "healthy" if assistant else "initializing",
        "architecture": "MCP (FastMCP/SSE) + LangChain + Ollama"
    }

@app.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest):
    if not assistant:
        raise HTTPException(503, "Assistant not ready")
    
    try:
        logger.info(f"Processing: {request.message[:50]}...")
        response = await assistant.chat(request.message)
        return ChatResponse(response=response)
    except Exception as e:
        logger.error(f"Error: {e}")
        raise HTTPException(500, f"Error: {str(e)}")

@app.get("/health")
async def health():
    return {
        "status": "healthy" if assistant else "initializing",
        "assistant_connected": assistant is not None,
        "tools": len(assistant.tools) if assistant else 0
    }

@app.get("/tools")
async def list_tools():
    if not assistant:
        return {"tools": [], "status": "not_ready"}
    
    return {
        "tools": [
            {"name": tool.name, "description": tool.description}
            for tool in assistant.tools
        ],
        "status": "ready"
    }

@app.post("/reset")
async def reset_chat():
    """Resets the chatbot memory and history"""
    if not assistant:
        raise HTTPException(503, "Assistant not ready")
    
    try:
        assistant.reset_session()
        logger.info("Chat session reset requested by client.")
        return {"status": "success", "message": "Memory wiped"}
    except Exception as e:
        logger.error(f"Error resetting: {e}")
        raise HTTPException(500, str(e))

if __name__ == "__main__":
    import uvicorn
    logger.info(" Starting MCP API Server...")
    uvicorn.run(app, host="0.0.0.0", port=8001)