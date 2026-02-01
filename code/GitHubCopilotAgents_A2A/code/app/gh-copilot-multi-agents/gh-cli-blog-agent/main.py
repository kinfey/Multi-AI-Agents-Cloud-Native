"""Blog Agent - GitHub Copilot SDK Blog Generation Service

This agent provides blog generation capabilities by integrating:
- GitHub Copilot SDK for AI-powered content generation
- Blog Skill with DeepSearch for research-based writing
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
AGENT_PORT = int(os.getenv("AGENT_PORT", "8001"))
AGENT_BASE_URL = "https://gh-cli-blog-agent.braveriver-c7642de6.swedencentral.azurecontainerapps.io"  # External URL for agent card
WORK_DIR = os.getcwd()
SKILLS_DIR = os.path.join(WORK_DIR, ".copilot_skills/blog/SKILL.md")
BLOG_DIR = os.path.join(WORK_DIR, "blog")

# Global variables
copilot_client: Optional[CopilotClient] = None
current_session = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan - initialize Copilot client on startup"""
    global copilot_client, BLOG_DIR
    
    logger.info("🚀 Starting Blog Agent...")
    logger.info(f"Work Directory: {WORK_DIR}")
    logger.info(f"Skills Directory: {SKILLS_DIR}")
    logger.info(f"Blog Directory: {BLOG_DIR}")
    
    try:
        # Check and create blog folder if not exists
        if not os.path.exists(BLOG_DIR):
            os.makedirs(BLOG_DIR)
            logger.info(f"✅ Created blog folder at: {BLOG_DIR}")
        else:
            logger.info(f"✅ Blog folder exists at: {BLOG_DIR}")
        
        # Initialize Copilot Client
        copilot_client = CopilotClient()
        await copilot_client.start()
        
        logger.info("✅ Blog Agent initialized successfully")
        
    except Exception as e:
        logger.error(f"❌ Failed to initialize Blog Agent: {e}")
        raise
    
    yield
    
    # Cleanup
    logger.info("🛑 Shutting down Blog Agent...")
    if copilot_client:
        await copilot_client.stop()


# FastAPI app
app = FastAPI(
    title="Blog Agent",
    description="GitHub Copilot SDK-based Blog Generation Agent with DeepSearch",
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
    agent: str = "blog_agent"
    blog_path: Optional[str] = None
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
                    "parts": [{"kind": "text", "text": "Processing your request..."}]
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
                            "parts": [{"kind": "text", "text": f"Still processing... ({heartbeat_count * 5}s elapsed, {len(response_chunks)} chunks received)"}]
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
            response_text = "Task completed successfully."
        
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
        
        return response_text if response_text else "Task completed successfully."
        
    except Exception as e:
        logger.error(f"❌ Error processing A2A message: {e}", exc_info=True)
        return f"Error: {str(e)}"

async def generate_sse_response(jsonrpc: str, request_id: str, task_id: str, context_id: str, result_text: str):
    """Generate Server-Sent Events response for A2A protocol
    
    A2A Protocol SSE Event Types:
    - TaskStatusUpdateEvent: status updates with contextId, taskId, final, status
    - TaskArtifactUpdateEvent: artifact updates with contextId, taskId, artifact
    
    Each message inside status needs: messageId, role, parts
    """
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
                    "parts": [{"kind": "text", "text": "Processing your request..."}]
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
            
            logger.info(f"📝 Processing message: {message_text[:100]}...")
            
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
        "agent": "blog_agent",
        "status": "running",
        "capabilities": ["blog_generation", "technical_writing", "content_research"],
        "skills": ["blog_skill_with_deepsearch"]
    }


@app.get("/health")
async def health():
    """Health check endpoint"""
    if copilot_client is None:
        raise HTTPException(status_code=503, detail="Agent not initialized")
    return {"status": "healthy"}


@app.get("/blog/{filename}")
async def download_blog(filename: str):
    """
    Download a blog file by filename
    
    Example: GET /blog/blog-2026-01-30.md
    """
    file_path = Path(BLOG_DIR) / filename
    
    if not file_path.exists():
        raise HTTPException(status_code=404, detail=f"Blog file '{filename}' not found")
    
    if not file_path.suffix == ".md":
        raise HTTPException(status_code=400, detail="Only .md files are allowed")
    
    return FileResponse(
        path=str(file_path),
        filename=filename,
        media_type="text/markdown"
    )


@app.get("/blogs")
async def list_blogs():
    """
    List all available blog files
    
    Returns a list of blog files with download URLs
    """
    try:
        blog_files = list(Path(BLOG_DIR).glob("blog-*.md"))
        blogs = []
        for blog_file in sorted(blog_files, reverse=True):
            blogs.append({
                "filename": blog_file.name,
                "download_url": f"/blog/{blog_file.name}",
                "size": blog_file.stat().st_size,
                "modified": datetime.fromtimestamp(blog_file.stat().st_mtime).isoformat()
            })
        return {"blogs": blogs, "total": len(blogs)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error listing blogs: {str(e)}")


@app.post("/task", response_model=TaskResponse)
async def execute_task(request: TaskRequest):
    """
    Execute a blog generation task
    
    Example requests:
    - "Write a blog post about Docker containerization"
    - "Create a technical blog on Kubernetes best practices"
    - "Generate a blog about Python async programming"
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
        
        # Enhanced prompt to leverage the blog skill
        enhanced_prompt = f"""
{request.task}

Please follow the Blog Skill guidelines:
1. Use DeepSearch to research each section thoroughly
2. Write from a technical evangelist perspective - be engaging, inspiring, and enthusiastic
3. Include practical code examples
4. Save the blog as blog-{datetime.now().strftime('%Y-%m-%d')}.md in the blog folder
5. Include proper metadata, SEO optimization, and citations
        """
        
        # Execute task with timeout
        await session.send_and_wait({"prompt": enhanced_prompt}, timeout=600)
        
        # Combine response
        response_text = ''.join(response_chunks)
        
        # Find the generated blog file
        blog_path = None
        download_url = None
        try:
            # Look for the most recently created blog file
            blog_files = list(Path(BLOG_DIR).glob("blog-*.md"))
            if blog_files:
                # Get the most recent file
                latest_blog = max(blog_files, key=lambda p: p.stat().st_mtime)
                blog_path = str(latest_blog.absolute())
                # Create a relative download path
                download_url = f"/blog/{latest_blog.name}"
                logger.info(f"📄 Generated blog: {blog_path}")
        except Exception as e:
            logger.warning(f"Could not determine blog path: {e}")
        
        logger.info(f"✅ Task completed for {request.user_id}")
        
        return TaskResponse(
            result=response_text if response_text else "Blog post generated successfully. Check the blog folder.",
            agent="blog_agent",
            blog_path=blog_path,
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
    # Use configured base URL (from env var or default to localhost)
    # base_url = AGENT_BASE_URL
    base_url = ''
    
    return JSONResponse({
        "name": "blog_agent",
        "description": "Specialized blog generation agent with DeepSearch research and technical evangelist writing style",
        "version": "1.0.0",
        "url": "Your Blog Agent URL on Azure Container Apps Endpoint",
        "protocol": "a2a",
        "protocolVersion": "0.2.0",
        
        # Required A2A fields
        "defaultInputModes": ["text"],
        "defaultOutputModes": ["text"],
        
        # Primary keywords for task routing (custom extension for multi-agent orchestration)
        "primaryKeywords": ["blog", "blog post", "article", "write a post", "writing", "content research", "write about", "write a"],
        
        # Skills at root level (required by A2A protocol)
        "skills": [
            {
                "id": "blog_generation",
                "name": "Blog Generation",
                "description": "Generate comprehensive, well-researched technical blog posts with DeepSearch integration",
                "tags": ["blog", "writing", "content"],
                "examples": [
                    "Write a blog post about GitHub Copilot SDK",
                    "Create a technical blog on microservices architecture",
                    "Generate a blog about AI-powered development tools"
                ]
            },
            {
                "id": "technical_writing",
                "name": "Technical Writing",
                "description": "Write technical content from an evangelist perspective - engaging, educational, and inspiring",
                "tags": ["technical", "tutorial", "guide"],
                "examples": [
                    "Write a tutorial on Docker containerization",
                    "Create a guide for Kubernetes deployment",
                    "Explain async programming in Python"
                ]
            },
            {
                "id": "content_research",
                "name": "Content Research",
                "description": "Research topics using DeepSearch to gather current information, examples, and best practices",
                "tags": ["research", "analysis", "information"],
                "examples": [
                    "Research latest Azure services",
                    "Find best practices for cloud architecture",
                    "Gather information about AI trends 2026"
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
    
    logger.info(f"📝 Starting Blog Agent on port {AGENT_PORT}")
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=AGENT_PORT,
        log_level="info"
    )
