"""PPT Agent - GitHub Copilot SDK PPT Generation Service

This agent provides PPT generation capabilities by integrating:
- GitHub Copilot SDK for AI-powered content generation
- PPT Skill for presentation creation
- FastAPI endpoints for A2A protocol compatibility

Architecture:
- Uses CopilotClient from GitHub Copilot SDK
- Exposes A2A protocol endpoints for discoverability
- Can be called by Orchestrator or other agents
"""

import os
import logging
import asyncio
import sys
import uuid
from typing import Optional, Any, Dict, List
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from copilot import CopilotClient
from copilot.generated.session_events import SessionEventType

# Load environment variables from .env file
load_dotenv()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Configuration
AGENT_PORT = int(os.getenv("AGENT_PORT", "8002"))
AGENT_BASE_URL = os.getenv("AGENT_BASE_URL", "http://localhost:8002")
WORK_DIR = os.getcwd()
SKILLS_DIR = os.path.join(WORK_DIR, ".copilot_skills/ppt/SKILL.md")
PPT_DIR = os.path.join(WORK_DIR, "ppt")

# Global variables
copilot_client: Optional[CopilotClient] = None
current_session = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan - initialize Copilot client on startup"""
    global copilot_client, PPT_DIR
    
    logger.info("🚀 Starting PPT Agent...")
    logger.info(f"Work Directory: {WORK_DIR}")
    logger.info(f"Skills Directory: {SKILLS_DIR}")
    logger.info(f"PPT Directory: {PPT_DIR}")
    
    try:
        # Check and create ppt folder if not exists
        if not os.path.exists(PPT_DIR):
            os.makedirs(PPT_DIR)
            logger.info(f"✅ Created ppt folder at: {PPT_DIR}")
        else:
            logger.info(f"✅ PPT folder exists at: {PPT_DIR}")
        
        # Initialize Copilot Client
        copilot_client = CopilotClient()
        await copilot_client.start()
        
        logger.info("✅ PPT Agent initialized successfully")
        
    except Exception as e:
        logger.error(f"❌ Failed to initialize PPT Agent: {e}")
        raise
    
    yield
    
    # Cleanup
    logger.info("🛑 Shutting down PPT Agent...")
    if copilot_client:
        await copilot_client.stop()


# FastAPI app
app = FastAPI(
    title="PPT Agent",
    description="GitHub Copilot SDK-based PPT Generation Agent",
    version="1.0.0",
    lifespan=lifespan
)


class TaskRequest(BaseModel):
    """Request model for task execution"""
    task: str
    user_id: Optional[str] = "anonymous"


class TaskResponse(BaseModel):
    """Response model for task execution"""
    result: str
    agent: str = "ppt_agent"
    ppt_path: Optional[str] = None
    download_url: Optional[str] = None


# A2A JSON-RPC Models
class A2AMessage(BaseModel):
    """A2A Protocol Message"""
    role: str
    parts: List[Dict[str, Any]]


class A2ATaskParams(BaseModel):
    """A2A Task Parameters"""
    id: Optional[str] = None
    message: A2AMessage


class A2ARequest(BaseModel):
    """A2A JSON-RPC Request"""
    jsonrpc: str = "2.0"
    id: Optional[str] = None
    method: str
    params: Optional[Dict[str, Any]] = None


async def process_a2a_message_streaming(message_text: str, jsonrpc: str, request_id: str, task_id: str, context_id: str):
    """Process an A2A message and yield SSE events as the response is generated"""
    import json
    global copilot_client
    
    message_id_working = str(uuid.uuid4())
    message_id_complete = str(uuid.uuid4())
    artifact_id = str(uuid.uuid4())
    
    # Send initial working status immediately
    status_event = {
        "jsonrpc": jsonrpc,
        "id": request_id,
        "result": {
            "contextId": context_id,
            "taskId": task_id,
            "final": False,
            "status": {
                "state": "working",
                "message": {
                    "messageId": message_id_working,
                    "role": "agent",
                    "parts": [{"kind": "text", "text": "Processing your PPT request..."}]
                }
            },
            "kind": "status-update"
        }
    }
    yield f"data: {json.dumps(status_event)}\n\n"
    
    if copilot_client is None:
        error_event = {
            "jsonrpc": jsonrpc,
            "id": request_id,
            "result": {
                "contextId": context_id,
                "taskId": task_id,
                "final": True,
                "status": {
                    "state": "failed",
                    "message": {
                        "messageId": str(uuid.uuid4()),
                        "role": "agent",
                        "parts": [{"kind": "text", "text": "Agent not initialized"}]
                    }
                },
                "kind": "status-update"
            }
        }
        yield f"data: {json.dumps(error_event)}\n\n"
        return
    
    try:
        # Create a new session for this task
        session = await copilot_client.create_session({
            "model": "claude-sonnet-4.5",
            "streaming": True,
            "skill_directories": [SKILLS_DIR]
        })
        
        logger.info(f"✓ A2A Session created with ID: {session.session_id}")
        
        # Collect the response
        response_chunks = []
        chunk_count = 0
        
        def handle_event(event):
            nonlocal chunk_count
            if event.type == SessionEventType.ASSISTANT_MESSAGE_DELTA:
                response_chunks.append(event.data.delta_content)
                chunk_count += 1
        
        session.on(handle_event)
        
        # Start the task (non-blocking)
        task = asyncio.create_task(session.send_and_wait({"prompt": message_text}, timeout=600))
        
        # Send periodic heartbeat events while waiting
        heartbeat_count = 0
        while not task.done():
            await asyncio.sleep(5)  # Check every 5 seconds
            heartbeat_count += 1
            
            # Send a working status update to keep connection alive
            heartbeat_event = {
                "jsonrpc": jsonrpc,
                "id": request_id,
                "result": {
                    "contextId": context_id,
                    "taskId": task_id,
                    "final": False,
                    "status": {
                        "state": "working",
                        "message": {
                            "messageId": str(uuid.uuid4()),
                            "role": "agent",
                            "parts": [{"kind": "text", "text": f"Still generating PPT... ({heartbeat_count * 5}s elapsed, {len(response_chunks)} chunks received)"}]
                        }
                    },
                    "kind": "status-update"
                }
            }
            yield f"data: {json.dumps(heartbeat_event)}\n\n"
        
        # Wait for the task to complete and get any exception
        await task
        
        # Combine response
        response_text = ''.join(response_chunks)
        if not response_text:
            response_text = "PPT generation completed successfully."
        
        # Send the artifact with result
        artifact_event = {
            "jsonrpc": jsonrpc,
            "id": request_id,
            "result": {
                "contextId": context_id,
                "taskId": task_id,
                "artifact": {
                    "artifactId": artifact_id,
                    "parts": [
                        {
                            "kind": "text",
                            "text": response_text
                        }
                    ]
                },
                "kind": "artifact-update"
            }
        }
        yield f"data: {json.dumps(artifact_event)}\n\n"
        
        # Send completion status
        complete_event = {
            "jsonrpc": jsonrpc,
            "id": request_id,
            "result": {
                "contextId": context_id,
                "taskId": task_id,
                "final": True,
                "status": {
                    "state": "completed",
                    "message": {
                        "messageId": message_id_complete,
                        "role": "agent",
                        "parts": [{"kind": "text", "text": response_text}]
                    }
                },
                "kind": "status-update"
            }
        }
        yield f"data: {json.dumps(complete_event)}\n\n"
        
    except Exception as e:
        logger.error(f"❌ Error processing A2A message: {e}", exc_info=True)
        error_event = {
            "jsonrpc": jsonrpc,
            "id": request_id,
            "result": {
                "contextId": context_id,
                "taskId": task_id,
                "final": True,
                "status": {
                    "state": "failed",
                    "message": {
                        "messageId": str(uuid.uuid4()),
                        "role": "agent",
                        "parts": [{"kind": "text", "text": f"Error: {str(e)}"}]
                    }
                },
                "kind": "status-update"
            }
        }
        yield f"data: {json.dumps(error_event)}\n\n"


async def process_a2a_message(message_text: str) -> str:
    """Process an A2A message and return the response (legacy non-streaming)"""
    global copilot_client
    
    if copilot_client is None:
        return "Agent not initialized"
    
    try:
        # Create a new session for this task
        session = await copilot_client.create_session({
            "model": "claude-sonnet-4.5",
            "streaming": True,
            "skill_directories": [SKILLS_DIR]
        })
        
        logger.info(f"✓ A2A Session created with ID: {session.session_id}")
        
        # Collect the response
        response_chunks = []
        
        def handle_event(event):
            if event.type == SessionEventType.ASSISTANT_MESSAGE_DELTA:
                response_chunks.append(event.data.delta_content)
        
        session.on(handle_event)
        
        # Execute task with timeout
        await session.send_and_wait({"prompt": message_text}, timeout=600)
        
        # Combine response
        response_text = ''.join(response_chunks)
        
        return response_text if response_text else "PPT generation completed successfully."
        
    except Exception as e:
        logger.error(f"❌ Error processing A2A message: {e}", exc_info=True)
        return f"Error: {str(e)}"


async def generate_sse_response(jsonrpc: str, request_id: str, task_id: str, context_id: str, result_text: str):
    """Generate Server-Sent Events response for A2A protocol"""
    import json
    
    message_id_working = str(uuid.uuid4())
    message_id_complete = str(uuid.uuid4())
    artifact_id = str(uuid.uuid4())
    
    # Send task status update - working
    status_event = {
        "jsonrpc": jsonrpc,
        "id": request_id,
        "result": {
            "contextId": context_id,
            "taskId": task_id,
            "final": False,
            "status": {
                "state": "working",
                "message": {
                    "messageId": message_id_working,
                    "role": "agent",
                    "parts": [{"kind": "text", "text": "Processing your PPT request..."}]
                }
            },
            "kind": "status-update"
        }
    }
    yield f"data: {json.dumps(status_event)}\n\n"
    
    # Send the artifact with result
    artifact_event = {
        "jsonrpc": jsonrpc,
        "id": request_id,
        "result": {
            "contextId": context_id,
            "taskId": task_id,
            "artifact": {
                "artifactId": artifact_id,
                "parts": [
                    {
                        "kind": "text",
                        "text": result_text
                    }
                ]
            },
            "kind": "artifact-update"
        }
    }
    yield f"data: {json.dumps(artifact_event)}\n\n"
    
    # Send completion status
    complete_event = {
        "jsonrpc": jsonrpc,
        "id": request_id,
        "result": {
            "contextId": context_id,
            "taskId": task_id,
            "final": True,
            "status": {
                "state": "completed",
                "message": {
                    "messageId": message_id_complete,
                    "role": "agent",
                    "parts": [{"kind": "text", "text": result_text}]
                }
            },
            "kind": "status-update"
        }
    }
    yield f"data: {json.dumps(complete_event)}\n\n"


@app.post("/")
async def a2a_jsonrpc_endpoint(request: Request):
    """
    A2A Protocol JSON-RPC 2.0 Endpoint with SSE Streaming
    
    Handles A2A protocol messages including:
    - message/send: Send a message to the agent
    - message/stream: Stream a message to the agent
    - tasks/send: Send a task to the agent
    - tasks/sendSubscribe: Subscribe to task updates
    
    Returns Server-Sent Events (SSE) stream as required by A2A protocol
    """
    try:
        body = await request.json()
        logger.info(f"📥 A2A Request: {body.get('method', 'unknown')}")
        
        jsonrpc = body.get("jsonrpc", "2.0")
        request_id = body.get("id")
        method = body.get("method", "")
        params = body.get("params", {})
        
        # Handle different A2A methods
        if method in ["message/send", "message/stream", "tasks/send", "tasks/sendSubscribe"]:
            # Extract message from params
            message = params.get("message", {})
            parts = message.get("parts", [])
            
            # Extract text from parts
            message_text = ""
            for part in parts:
                if part.get("kind") == "text" or "text" in part:
                    message_text += part.get("text", "")
            
            if not message_text:
                message_text = str(params)
            
            logger.info(f"📝 Processing PPT request: {message_text[:100]}...")
            
            # Generate task/message ID and context ID
            task_id = params.get("id") or str(uuid.uuid4())
            context_id = params.get("contextId") or str(uuid.uuid4())
            
            # Return SSE streaming response with heartbeat support
            return StreamingResponse(
                process_a2a_message_streaming(message_text, jsonrpc, request_id, task_id, context_id),
                media_type="text/event-stream",
                headers={
                    "Cache-Control": "no-cache",
                    "Connection": "keep-alive",
                    "X-Accel-Buffering": "no"
                }
            )
        
        elif method == "tasks/get":
            # Return task status as SSE
            task_id = params.get("id", "unknown")
            context_id = params.get("contextId") or str(uuid.uuid4())
            
            async def status_sse():
                import json
                event = {
                    "jsonrpc": jsonrpc,
                    "id": request_id,
                    "result": {
                        "contextId": context_id,
                        "taskId": task_id,
                        "final": True,
                        "status": {
                            "state": "completed"
                        },
                        "kind": "status-update"
                    }
                }
                yield f"data: {json.dumps(event)}\n\n"
            
            return StreamingResponse(
                status_sse(),
                media_type="text/event-stream"
            )
        
        else:
            # Unknown method - return error as SSE
            async def error_sse():
                import json
                event = {
                    "jsonrpc": jsonrpc,
                    "id": request_id,
                    "error": {
                        "code": -32601,
                        "message": f"Method not found: {method}"
                    }
                }
                yield f"data: {json.dumps(event)}\n\n"
            
            return StreamingResponse(
                error_sse(),
                media_type="text/event-stream"
            )
            
    except Exception as e:
        logger.error(f"❌ A2A Error: {e}", exc_info=True)
        
        async def exception_sse():
            import json
            event = {
                "jsonrpc": "2.0",
                "id": None,
                "error": {
                    "code": -32603,
                    "message": str(e)
                }
            }
            yield f"data: {json.dumps(event)}\n\n"
        
        return StreamingResponse(
            exception_sse(),
            media_type="text/event-stream"
        )


@app.get("/")
async def root():
    """Root endpoint"""
    return {
        "agent": "ppt_agent",
        "status": "running",
        "capabilities": ["ppt_generation", "presentation_creation", "slide_design"],
        "skills": ["ppt_skill"]
    }


@app.get("/health")
async def health():
    """Health check endpoint"""
    if copilot_client is None:
        raise HTTPException(status_code=503, detail="Agent not initialized")
    return {"status": "healthy"}


@app.get("/ppt/{filename}")
async def download_ppt(filename: str):
    """
    Download a PPT file by filename
    
    Example: GET /ppt/presentation-2026-01-30.pptx
    """
    file_path = Path(PPT_DIR) / filename
    
    if not file_path.exists():
        raise HTTPException(status_code=404, detail=f"PPT file '{filename}' not found")
    
    # Allow common presentation formats
    allowed_extensions = [".pptx", ".ppt", ".pdf", ".md"]
    if file_path.suffix.lower() not in allowed_extensions:
        raise HTTPException(status_code=400, detail="Only presentation files are allowed")
    
    media_types = {
        ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        ".ppt": "application/vnd.ms-powerpoint",
        ".pdf": "application/pdf",
        ".md": "text/markdown"
    }
    
    return FileResponse(
        path=str(file_path),
        filename=filename,
        media_type=media_types.get(file_path.suffix.lower(), "application/octet-stream")
    )


@app.get("/ppts")
async def list_ppts():
    """
    List all available PPT files
    
    Returns a list of PPT files with download URLs
    """
    try:
        # Search for various presentation formats
        ppt_files = []
        for ext in ["*.pptx", "*.ppt", "*.pdf", "*.md"]:
            ppt_files.extend(list(Path(PPT_DIR).glob(ext)))
        
        ppts = []
        for ppt_file in sorted(ppt_files, key=lambda p: p.stat().st_mtime, reverse=True):
            ppts.append({
                "filename": ppt_file.name,
                "download_url": f"/ppt/{ppt_file.name}",
                "size": ppt_file.stat().st_size,
                "modified": datetime.fromtimestamp(ppt_file.stat().st_mtime).isoformat()
            })
        return {"ppts": ppts, "total": len(ppts)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error listing PPTs: {str(e)}")


@app.post("/task", response_model=TaskResponse)
async def execute_task(request: TaskRequest):
    """
    Execute a PPT generation task
    
    Example requests:
    - "Create a PPT about Microsoft Agent Framework"
    - "Generate a presentation on Kubernetes architecture"
    - "Make slides about AI development best practices"
    """
    if copilot_client is None:
        raise HTTPException(status_code=503, detail="Agent not initialized")
    
    logger.info(f"📝 Task from {request.user_id}: {request.task}")
    
    try:
        # Create a new session for this task
        session = await copilot_client.create_session({
            "model": "claude-sonnet-4.5",
            "streaming": True,
            "skill_directories": [SKILLS_DIR]
        })
        
        logger.info(f"✓ Session created with ID: {session.session_id}")
        
        # Collect the response
        response_chunks = []
        
        def handle_event(event):
            if event.type == SessionEventType.ASSISTANT_MESSAGE_DELTA:
                response_chunks.append(event.data.delta_content)
        
        session.on(handle_event)
        
        # Enhanced prompt to leverage the PPT skill
        enhanced_prompt = f"""
{request.task}

Please follow the PPT Skill guidelines:
1. Research the topic thoroughly
2. Create well-structured presentation slides
3. Include practical code examples where relevant
4. Save the presentation in the ppt folder
5. Follow all the requirements specified in the skill documentation
        """
        
        # Execute task with timeout (10 minutes for complex PPT generation)
        await session.send_and_wait({"prompt": enhanced_prompt}, timeout=600)
        
        # Combine response
        response_text = ''.join(response_chunks)
        
        # Find the generated PPT file
        ppt_path = None
        download_url = None
        try:
            # Look for the most recently created PPT file
            ppt_files = []
            for ext in ["*.pptx", "*.ppt", "*.pdf", "*.md"]:
                ppt_files.extend(list(Path(PPT_DIR).glob(ext)))
            
            if ppt_files:
                # Get the most recent file
                latest_ppt = max(ppt_files, key=lambda p: p.stat().st_mtime)
                ppt_path = str(latest_ppt.absolute())
                # Create a relative download path
                download_url = f"/ppt/{latest_ppt.name}"
                logger.info(f"📄 Generated PPT: {ppt_path}")
        except Exception as e:
            logger.warning(f"Could not determine PPT path: {e}")
        
        logger.info(f"✅ Task completed for {request.user_id}")
        
        return TaskResponse(
            result=response_text if response_text else "PPT generated successfully. Check the ppt folder.",
            agent="ppt_agent",
            ppt_path=ppt_path,
            download_url=download_url
        )
        
    except Exception as e:
        logger.error(f"❌ Error executing task: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Error executing task: {str(e)}")


@app.get("/.well-known/agent-card.json")
async def agent_card():
    """
    A2A Protocol: Agent Card endpoint
    
    This endpoint exposes the agent's capabilities for discovery by other agents
    and the orchestrator.
    
    Follows A2A Protocol specification: https://a2a-protocol.org/latest/
    """
    return JSONResponse({
        "name": "ppt_agent",
        "description": "Specialized PPT generation agent for creating professional presentations with code examples",
        "version": "1.0.0",
        "url": "Your PPT Agent URL on Azure Container Apps Endpoint",
        "protocol": "a2a",
        "protocolVersion": "0.2.0",
        
        # Required A2A fields
        "defaultInputModes": ["text"],
        "defaultOutputModes": ["text"],
        
        # Primary keywords for task routing (custom extension for multi-agent orchestration)
        "primaryKeywords": ["ppt", "powerpoint", "presentation", "slides", "slide deck", "create ppt", "create a ppt", "make slides"],
        
        # Skills at root level (required by A2A protocol)
        "skills": [
            {
                "id": "ppt_generation",
                "name": "PPT Generation",
                "description": "Generate comprehensive, well-structured presentations with code examples",
                "tags": ["ppt", "presentation", "slides"],
                "examples": [
                    "Create a PPT about Microsoft Agent Framework",
                    "Generate a presentation on Kubernetes architecture",
                    "Make slides about GitHub Copilot SDK"
                ]
            },
            {
                "id": "technical_presentation",
                "name": "Technical Presentation",
                "description": "Create technical presentations with diagrams, code snippets, and best practices",
                "tags": ["technical", "tutorial", "guide"],
                "examples": [
                    "Create a technical presentation on Docker containerization",
                    "Generate slides for a Kubernetes workshop",
                    "Make a presentation about microservices architecture"
                ]
            },
            {
                "id": "code_showcase",
                "name": "Code Showcase",
                "description": "Create presentations that showcase code examples and implementations",
                "tags": ["code", "examples", "demo"],
                "examples": [
                    "Create slides showcasing Python async patterns",
                    "Generate a code walkthrough presentation",
                    "Make slides demonstrating API usage"
                ]
            }
        ],
        
        # Additional capabilities info
        "capabilities": {
            "streaming": True,
            "pushNotifications": False,
            "stateTransitionHistory": False
        },
        
        # Provider information
        "provider": {
            "organization": "Kinfey Lo",
            "url": "https://github.com/kinfey"
        }
    })


if __name__ == "__main__":
    import uvicorn
    
    logger.info(f"📊 Starting PPT Agent on port {AGENT_PORT}")
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=AGENT_PORT,
        log_level="info"
    )
