"""Blog Agent - GitHub Copilot SDK Blog Generation Service

This agent provides blog generation capabilities by integrating:
- GitHub Copilot SDK for AI-powered content generation
- Skill Server sidecar for blog skill management
- FastAPI REST endpoints for blog generation and management

Architecture:
- Uses CopilotClient from GitHub Copilot SDK
- Reads skills from shared volume (synced by skill-server sidecar)
- Runs as a sidecar container, writes blogs to shared volume
"""

import os
import logging
import asyncio
from typing import Optional
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel
from copilot import CopilotClient
from copilot.generated.session_events import SessionEventType
import httpx

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

AGENT_PORT = int(os.getenv("AGENT_PORT", "8001"))
SKILL_SERVER_URL = os.getenv("SKILL_SERVER_URL", "http://127.0.0.1:8002")
WORK_DIR = os.getcwd()
# Skills are synced to shared volume by skill-server sidecar
SKILLS_DIR = os.getenv("SKILLS_DIR", os.path.join(WORK_DIR, ".copilot_skills/blog/SKILL.md"))
BLOG_DIR = os.path.join(WORK_DIR, "blog")

copilot_client: Optional[CopilotClient] = None


async def wait_for_skill_server(url: str, retries: int = 30, delay: float = 2.0):
    """Wait for the skill-server sidecar to become healthy."""
    async with httpx.AsyncClient() as client:
        for i in range(retries):
            try:
                resp = await client.get(f"{url}/health", timeout=5.0)
                if resp.status_code == 200:
                    logger.info(f"✅ Skill server is healthy at {url}")
                    return True
            except Exception:
                pass
            logger.info(f"⏳ Waiting for skill server... ({i + 1}/{retries})")
            await asyncio.sleep(delay)
    raise RuntimeError(f"Skill server at {url} did not become healthy")


@asynccontextmanager
async def lifespan(app: FastAPI):
    global copilot_client, BLOG_DIR

    logger.info("🚀 Starting Blog Agent (Copilot Sidecar)...")
    logger.info(f"Work Directory: {WORK_DIR}")
    logger.info(f"Skills Directory: {SKILLS_DIR}")
    logger.info(f"Blog Directory: {BLOG_DIR}")
    logger.info(f"Skill Server URL: {SKILL_SERVER_URL}")

    try:
        if not os.path.exists(BLOG_DIR):
            os.makedirs(BLOG_DIR)
            logger.info(f"✅ Created blog folder at: {BLOG_DIR}")
        else:
            logger.info(f"✅ Blog folder exists at: {BLOG_DIR}")

        # Wait for skill-server sidecar to be ready
        await wait_for_skill_server(SKILL_SERVER_URL)

        # Verify skill files are available on shared volume
        skill_path = Path(SKILLS_DIR)
        if skill_path.exists():
            logger.info(f"✅ Skill file found: {SKILLS_DIR}")
        else:
            logger.warning(f"⚠️  Skill file not yet at {SKILLS_DIR}, requesting sync...")
            async with httpx.AsyncClient() as client:
                await client.post(f"{SKILL_SERVER_URL}/sync", timeout=10.0)
            await asyncio.sleep(1)

        copilot_client = CopilotClient()
        await copilot_client.start()
        logger.info("✅ Blog Agent initialized successfully")

    except Exception as e:
        logger.error(f"❌ Failed to initialize Blog Agent: {e}")
        raise

    yield

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
    if copilot_client is None:
        raise HTTPException(status_code=503, detail="Agent not initialized")
    # Check skill-server sidecar health
    skill_healthy = False
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(f"{SKILL_SERVER_URL}/health", timeout=3.0)
            skill_healthy = resp.status_code == 200
    except Exception:
        pass
    return {"status": "healthy", "skill_server": "healthy" if skill_healthy else "unreachable"}


@app.get("/blog/{filename}")
async def download_blog(filename: str):
    """Download a blog file by filename"""
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
    """List all available blog files"""
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
    """Execute a blog generation task"""
    if copilot_client is None:
        raise HTTPException(status_code=503, detail="Agent not initialized")

    logger.info(f"📝 Task from {request.user_id}: {request.task}")

    try:
        session = await copilot_client.create_session({
            "model": "claude-sonnet-4.5",
            "streaming": True,
            "skill_directories": [SKILLS_DIR]
        })

        logger.info(f"✓ Session created with ID: {session.session_id}")

        response_chunks = []

        def handle_event(event):
            if event.type == SessionEventType.ASSISTANT_MESSAGE_DELTA:
                response_chunks.append(event.data.delta_content)

        session.on(handle_event)

        enhanced_prompt = f"""
{request.task}

Please follow the Blog Skill guidelines:
1. Use DeepSearch to research each section thoroughly
2. Write from a technical evangelist perspective - be engaging, inspiring, and enthusiastic
3. Include practical code examples
4. Save the blog as blog-{datetime.now().strftime('%Y-%m-%d')}.md in the blog folder
5. Include proper metadata, SEO optimization, and citations
        """

        await session.send_and_wait({"prompt": enhanced_prompt}, timeout=600)

        response_text = ''.join(response_chunks)

        # Find the generated blog file
        blog_path = None
        download_url = None
        try:
            blog_files = list(Path(BLOG_DIR).glob("blog-*.md"))
            if blog_files:
                latest_blog = max(blog_files, key=lambda p: p.stat().st_mtime)
                blog_path = str(latest_blog.absolute())
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


if __name__ == "__main__":
    import uvicorn

    logger.info(f"📝 Starting Blog Agent on port {AGENT_PORT}")
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=AGENT_PORT,
        log_level="info"
    )
