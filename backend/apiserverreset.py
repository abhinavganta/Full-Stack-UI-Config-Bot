from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from contextlib import asynccontextmanager
from clientreset import MCPAIAssistant
import logging

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"], 
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

assistant = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage application lifespan - initialize and cleanup"""
    global assistant
    try:
        assistant = MCPAIAssistant()
        await assistant.initialize()
        
        yield
    except Exception as e:
        logger.error(f"Failed to initialize: {e}")
        yield
    finally:
        
        if assistant:
            try:
                await assistant.close()
                
            except Exception as e:
                logger.error(f"Error during cleanup: {e}")


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

class ResetResponse(BaseModel):
    status: str
    message: str



@app.get("/")
async def root():
    """Root endpoint - health check"""
    return {
        "message": "MCP AI Assistant API",
        "status": "healthy" if assistant else "initializing",
        "architecture": "MCP + FastMCP/SSE + LangChain + Ollama"
    }

@app.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest):
    """Chat endpoint - process user messages"""
    if not assistant:
        logger.error(" Assistant not initialized")
        raise HTTPException(503, "Assistant not ready")
    
    try:
        logger.info(f"Processing: {request.message[:50]}...")
        response = await assistant.chat(request.message)
        logger.info(f"Response generated ({len(response)} chars)")
        return ChatResponse(response=response)
    except Exception as e:
        logger.error(f" Chat error: {e}", exc_info=True)
        raise HTTPException(500, f"Error: {str(e)}")

@app.post("/reset", response_model=ResetResponse)
async def reset_conversation():
    """Reset endpoint - clears conversation state and starts fresh"""
    if not assistant:
        logger.error("Assistant not initialized for reset")
        raise HTTPException(503, "Assistant not ready")
    
    try:
        
        
        
        assistant.reset()

        logger.info("Ready for fresh conversation")
        
        
        return ResetResponse(
            status="success",
            message="Conversation reset successfully. Starting fresh."
        )
    except Exception as e:
        logger.error(f"Reset error: {e}", exc_info=True)
        logger.error("Failed to reset assistant state")
        raise HTTPException(500, f"Reset failed: {str(e)}")

@app.get("/health")
async def health():
    """Health check endpoint"""
    health_status = {
        "status": "healthy" if assistant else "initializing",
        "assistant_connected": assistant is not None,
        "tools": len(assistant.tools) if assistant else 0
    }
    logger.debug(f"Health check: {health_status}")
    return health_status

@app.get("/tools")
async def list_tools():
    """List available MCP tools"""
    if not assistant:
        logger.warning("Tools requested but assistant not ready")
        return {"tools": [], "status": "not_ready"}
    
    tools_list = [
        {"name": tool.name, "description": tool.description} 
        for tool in assistant.tools
    ]
    logger.info(f"Listed {len(tools_list)} available tools")
    return {
        "tools": tools_list,
        "status": "ready"
    }

if __name__ == "__main__":
    import uvicorn
    
    uvicorn.run(app, host="0.0.0.0", port=8001)
