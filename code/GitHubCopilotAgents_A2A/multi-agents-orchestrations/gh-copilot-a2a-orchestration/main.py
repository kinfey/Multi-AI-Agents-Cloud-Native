# Copyright (c) Microsoft. All rights reserved.
# A2A (Agent-to-Agent) Protocol Integration for Multi-Agent Orchestration

"""
Agent2Agent (A2A) Protocol Integration - Multi-Agent Orchestration with Auto-Routing

This module demonstrates how to connect to and communicate with multiple external agents
using the A2A protocol, with intelligent task routing based on agent capabilities.

Key Features:
- Auto-discovery of multiple A2A agents
- Intelligent task routing based on agent skills and keywords
- Fallback mechanisms when no specific agent matches
- Support for concurrent agent discovery

For more information about the A2A protocol specification, visit: https://a2a-protocol.org/latest/

Configuration:
- Set A2A_AGENT_HOST in .env file with comma-separated URLs
  (e.g., A2A_AGENT_HOST=https://ppt-agent.example.com,https://blog-agent.example.com)
- Ensure each agent exposes its AgentCard at /.well-known/agent.json

Usage:
    python main.py
"""

import asyncio
import os
import logging
import re
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any

import httpx
from dotenv import load_dotenv
from a2a.client import A2ACardResolver
from agent_framework.a2a import A2AAgent

# Load environment variables from .env file
load_dotenv()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


@dataclass
class AgentInfo:
    """Information about a discovered A2A agent with its capabilities."""
    agent: A2AAgent
    host: str
    name: str
    description: str
    skills: List[Dict[str, Any]] = field(default_factory=list)
    tags: List[str] = field(default_factory=list)
    examples: List[str] = field(default_factory=list)
    primary_keywords: List[str] = field(default_factory=list)  # Keywords from agent card for routing
    
    def matches_task(self, task: str, all_agents_keywords: Dict[str, List[str]] = None) -> float:
        """
        Calculate a relevance score for the task based on agent capabilities.
        
        Args:
            task: The task description to match
            all_agents_keywords: Dict of agent_name -> primary_keywords for negative scoring
            
        Returns:
            A relevance score (0.0 to 1.0)
        """
        task_lower = task.lower()
        score = 0.0
        
        # Check primary keywords from agent card (highest priority)
        for keyword in self.primary_keywords:
            if keyword.lower() in task_lower:
                score += 0.5  # Strong match for primary keywords
                logger.debug(f"Primary keyword match: '{keyword}' for agent '{self.name}'")
        
        # Negative scoring: penalize if task contains keywords for OTHER agents
        if all_agents_keywords:
            for other_agent, other_keywords in all_agents_keywords.items():
                if other_agent != self.name:
                    for keyword in other_keywords:
                        if keyword.lower() in task_lower:
                            score -= 0.3  # Penalize for keywords meant for other agents
        
        # Check tags (medium weight)
        for tag in self.tags:
            tag_lower = tag.lower()
            # Skip common/generic tags that don't help differentiation
            if tag_lower in ['technical', 'tutorial', 'guide', 'code', 'examples']:
                continue
            if tag_lower in task_lower:
                score += 0.2
        
        # Check skill IDs for exact matches (most specific)
        for skill in self.skills:
            skill_id = skill.get('id', '').lower()
            if skill_id:
                # Check if skill_id keywords appear in task
                skill_keywords = skill_id.replace('_', ' ').split()
                for kw in skill_keywords:
                    if len(kw) > 3 and kw in task_lower:
                        score += 0.15
        
        # Check agent name keywords
        agent_name_lower = self.name.lower()
        name_parts = re.findall(r'\b\w+\b', agent_name_lower)
        for part in name_parts:
            if part not in ['agent', 'the', 'a'] and part in task_lower:
                score += 0.25
        
        return max(0.0, min(score, 1.0))  # Clamp between 0.0 and 1.0


class MultiAgentOrchestrator:
    """
    Orchestrator for managing multiple A2A agents with intelligent task routing.
    """
    
    def __init__(self, http_client: httpx.AsyncClient):
        self.http_client = http_client
        self.agents: Dict[str, AgentInfo] = {}
        self.default_agent: Optional[str] = None
    
    async def discover_agent(self, agent_host: str) -> Optional[AgentInfo]:
        """
        Discover and connect to an A2A-compliant agent.
        
        Args:
            agent_host: The base URL of the A2A agent
            
        Returns:
            AgentInfo instance if successful, None otherwise
        """
        try:
            agent_host = agent_host.strip()
            logger.info(f"🔍 Discovering agent at: {agent_host}")
            
            # Initialize A2ACardResolver to fetch the agent card
            resolver = A2ACardResolver(httpx_client=self.http_client, base_url=agent_host)
            
            # Get agent card from /.well-known/agent.json
            agent_card = await resolver.get_agent_card()
            
            logger.info(f"✅ Found agent: {agent_card.name}")
            logger.info(f"   Description: {agent_card.description}")
            
            # Create A2A agent instance
            agent = A2AAgent(
                name=agent_card.name,
                description=agent_card.description,
                agent_card=agent_card,
                url=agent_host,
                httpx_client=self.http_client,
            )
            
            # Extract skills, tags, and examples from agent card
            skills = []
            tags = []
            examples = []
            
            if hasattr(agent_card, 'skills') and agent_card.skills:
                for skill in agent_card.skills:
                    skill_dict = {}
                    if hasattr(skill, 'id'):
                        skill_dict['id'] = skill.id
                    if hasattr(skill, 'name'):
                        skill_dict['name'] = skill.name
                    if hasattr(skill, 'description'):
                        skill_dict['description'] = skill.description
                    if hasattr(skill, 'tags') and skill.tags:
                        skill_dict['tags'] = list(skill.tags)
                        tags.extend(skill.tags)
                    if hasattr(skill, 'examples') and skill.examples:
                        skill_dict['examples'] = list(skill.examples)
                        examples.extend(skill.examples)
                    skills.append(skill_dict)
            
            # Extract primary keywords from agent card (custom extension)
            # Try multiple ways to access the field due to different parsing behaviors
            primary_keywords = []
            if hasattr(agent_card, 'primary_keywords') and agent_card.primary_keywords:
                primary_keywords = list(agent_card.primary_keywords)
            elif hasattr(agent_card, 'primaryKeywords') and agent_card.primaryKeywords:
                primary_keywords = list(agent_card.primaryKeywords)
            # Try accessing via model_extra for Pydantic models with extra fields
            elif hasattr(agent_card, 'model_extra') and agent_card.model_extra:
                if 'primaryKeywords' in agent_card.model_extra:
                    primary_keywords = list(agent_card.model_extra['primaryKeywords'])
            # Try via __dict__ or direct attribute lookup
            elif hasattr(agent_card, '__dict__'):
                if 'primaryKeywords' in agent_card.__dict__:
                    primary_keywords = list(agent_card.__dict__['primaryKeywords'])
            
            # Log agent capabilities
            if skills:
                logger.info(f"   Skills: {[s.get('name', s.get('id', 'unknown')) for s in skills]}")
            if tags:
                logger.info(f"   Tags: {list(set(tags))}")
            if primary_keywords:
                logger.info(f"   Primary Keywords: {primary_keywords}")
            
            return AgentInfo(
                agent=agent,
                host=agent_host,
                name=agent_card.name,
                description=agent_card.description or "",
                skills=skills,
                tags=list(set(tags)),
                examples=examples,
                primary_keywords=primary_keywords
            )
            
        except Exception as e:
            logger.error(f"❌ Failed to discover agent at {agent_host}: {e}")
            return None
    
    async def discover_all_agents(self, agent_hosts: List[str]) -> int:
        """
        Discover all agents from a list of hosts concurrently.
        
        Args:
            agent_hosts: List of agent host URLs
            
        Returns:
            Number of successfully discovered agents
        """
        # Discover agents concurrently
        tasks = [self.discover_agent(host) for host in agent_hosts]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        for result in results:
            if isinstance(result, AgentInfo):
                self.agents[result.name] = result
                if self.default_agent is None:
                    self.default_agent = result.name
            elif isinstance(result, Exception):
                logger.error(f"Discovery error: {result}")
        
        return len(self.agents)
    
    def select_agent(self, task: str) -> Optional[AgentInfo]:
        """
        Select the most appropriate agent for a task based on capabilities.
        
        Args:
            task: The task description
            
        Returns:
            The most suitable AgentInfo, or default agent if no strong match
        """
        if not self.agents:
            return None
        
        # Build a dict of all agents' primary keywords for negative scoring
        all_agents_keywords: Dict[str, List[str]] = {
            name: info.primary_keywords for name, info in self.agents.items()
        }
        
        # Calculate relevance scores for all agents
        scores: List[tuple[str, float]] = []
        for name, agent_info in self.agents.items():
            score = agent_info.matches_task(task, all_agents_keywords)
            scores.append((name, score))
            logger.debug(f"Agent '{name}' score for task: {score:.2f}")
        
        # Sort by score descending
        scores.sort(key=lambda x: x[1], reverse=True)
        
        # Log the scoring results
        logger.info(f"🎯 Agent selection scores:")
        for name, score in scores:
            logger.info(f"   {name}: {score:.2f}")
        
        # Return the highest scoring agent if score is above threshold
        best_agent, best_score = scores[0]
        if best_score > 0.1:  # Minimum threshold for a match
            return self.agents[best_agent]
        
        # Fall back to default agent
        if self.default_agent:
            logger.info(f"📌 No strong match, using default agent: {self.default_agent}")
            return self.agents[self.default_agent]
        
        return None
    
    async def send_task(self, task: str, agent_name: Optional[str] = None) -> tuple[str, str]:
        """
        Send a task to an agent (auto-selected or specified).
        
        Args:
            task: The task to send
            agent_name: Optional specific agent name to use
            
        Returns:
            Tuple of (response text, agent name used)
        """
        # Select agent
        if agent_name and agent_name in self.agents:
            agent_info = self.agents[agent_name]
        else:
            agent_info = self.select_agent(task)
        
        if not agent_info:
            raise ValueError("No suitable agent found for the task")
        
        logger.info(f"📤 Sending task to agent '{agent_info.name}': {task[:100]}...")
        
        try:
            response = await agent_info.agent.run(task)
            
            # Extract text from response messages
            result_texts = []
            for message in response.messages:
                if hasattr(message, 'text') and message.text:
                    result_texts.append(message.text)
            
            result = "\n".join(result_texts)
            logger.info(f"📥 Received response from '{agent_info.name}' ({len(result)} chars)")
            
            return result, agent_info.name
            
        except Exception as e:
            logger.error(f"❌ Error sending task to agent '{agent_info.name}': {e}")
            raise
    
    def list_agents(self) -> None:
        """Print a summary of all discovered agents."""
        print("\n" + "=" * 60)
        print("📋 Discovered Agents Summary")
        print("=" * 60)
        
        for name, info in self.agents.items():
            print(f"\n🤖 {name}")
            print(f"   Host: {info.host}")
            print(f"   Description: {info.description[:100]}...")
            if info.skills:
                print(f"   Skills: {[s.get('name', s.get('id')) for s in info.skills]}")
            if info.tags:
                print(f"   Tags: {info.tags}")
        
        print("\n" + "=" * 60)


async def main():
    """
    Main function demonstrating multi-agent orchestration with auto-routing.
    """
    # Get A2A agent hosts from environment (comma-separated)
    a2a_agent_hosts_str = os.getenv("A2A_AGENT_HOST", "")
    
    if not a2a_agent_hosts_str:
        raise ValueError(
            "A2A_AGENT_HOST environment variable is not set. "
            "Please set it in .env file with comma-separated URLs "
            "(e.g., A2A_AGENT_HOST=https://ppt-agent.example.com,https://blog-agent.example.com)"
        )
    
    # Parse comma-separated hosts
    agent_hosts = [host.strip() for host in a2a_agent_hosts_str.split(",") if host.strip()]
    
    print("=" * 60)
    print("🤖 Multi-Agent Orchestration - A2A Protocol with Auto-Routing")
    print("=" * 60)
    print(f"\n📡 Target Agents ({len(agent_hosts)}):")
    for host in agent_hosts:
        print(f"   - {host}")
    print()
    
    # Create HTTP client with extended timeout for long-running tasks (10 minutes)
    async with httpx.AsyncClient(timeout=httpx.Timeout(600.0, connect=30.0)) as http_client:
        
        # Initialize orchestrator
        orchestrator = MultiAgentOrchestrator(http_client)
        
        # Step 1: Discover all agents
        print("-" * 60)
        print("🔍 Discovering agents...")
        print("-" * 60)
        
        discovered_count = await orchestrator.discover_all_agents(agent_hosts)
        
        if discovered_count == 0:
            print("❌ Could not connect to any A2A agents. Please ensure:")
            print("   1. The agents are running at the specified URLs")
            print("   2. Each agent exposes /.well-known/agent.json")
            return
        
        print(f"\n✅ Successfully discovered {discovered_count} agent(s)")
        
        # List all discovered agents
        orchestrator.list_agents()
        
        # Step 2: Demonstrate auto-routing with different tasks
        print("\n" + "=" * 60)
        print("🎯 Auto-Routing Demo - Tasks will be routed to appropriate agents")
        print("=" * 60)
        
        # Define test tasks that should route to different agents
        test_tasks = [
            {
                "task": "What are your capabilities?",
                "description": "Query capabilities (should go to any agent)"
            },
            {
                "task": """
                        Write a blog post about GitHub Copilot SDK. Include the following sections:
                        1. Introduction to GitHub Copilot SDK
                        2. Key Features and Capabilities
                        3. How to Get Started with GitHub Copilot SDK
                        4. Use Cases and Examples
                        5. Conclusion and Future Prospects Ensure the blog post is well-structured, informative, and engaging.
                        6. Save the generated blog post as a markdown file in the '\''blog'\'' folder created earlier, named '\''blog_post_yyyy_mm_dd.md'\'', where '\''yyyy_mm_dd'\'' is the current date.
                        7. please reference any code snippets or examples from the official GitHub Copilot SDK documentation
                            - https://github.com/github/copilot-sdk/blob/main/python/README.md\n   
                            - https://github.com/github/copilot-sdk/blob/main/nodejs/README.md\n   
                            - https://github.com/github/copilot-sdk/blob/main/go/README.md\n   
                            - https://github.com/github/copilot-sdk/blob/main/dotnet/README.md

                """,
                "description": "Create a blog task"
            },
            {
                "task": """
                    create a ppt about https://github.com/microsoft/agent-framework , including code examples where relevant. Follow all the requirements specified in the skill documentation.
                """,
                "description": "Create a ppt task"
            },
        ]
        
        for i, task_info in enumerate(test_tasks, 1):
            task = task_info["task"]
            description = task_info["description"]
            
            print(f"\n{'─' * 60}")
            print(f"📝 Task {i}: {description}")
            print(f"   Request: {task[:80]}...")
            print(f"{'─' * 60}")
            
            try:
                response, used_agent = await orchestrator.send_task(task)
                print(f"\n🤖 Handled by: {used_agent}")
                print(f"📄 Response preview: {response[:500]}...")
                if len(response) > 500:
                    print(f"   ... ({len(response) - 500} more characters)")
            except Exception as e:
                print(f"❌ Error: {e}")
        
        print("\n" + "=" * 60)
        print("✅ Multi-Agent Orchestration Demo completed!")
        print("=" * 60)


async def interactive_mode():
    """
    Interactive mode for testing multi-agent orchestration.
    """
    # Get A2A agent hosts from environment
    a2a_agent_hosts_str = os.getenv("A2A_AGENT_HOST", "")
    
    if not a2a_agent_hosts_str:
        raise ValueError("A2A_AGENT_HOST environment variable is not set.")
    
    agent_hosts = [host.strip() for host in a2a_agent_hosts_str.split(",") if host.strip()]
    
    print("=" * 60)
    print("🤖 Multi-Agent Orchestration - Interactive Mode")
    print("=" * 60)
    
    async with httpx.AsyncClient(timeout=httpx.Timeout(600.0, connect=30.0)) as http_client:
        orchestrator = MultiAgentOrchestrator(http_client)
        
        print("🔍 Discovering agents...")
        discovered_count = await orchestrator.discover_all_agents(agent_hosts)
        
        if discovered_count == 0:
            print("❌ No agents available")
            return
        
        orchestrator.list_agents()
        
        print("\n📝 Enter your tasks (type 'quit' to exit, 'list' to show agents):")
        
        while True:
            try:
                user_input = input("\n> ").strip()
                
                if not user_input:
                    continue
                
                if user_input.lower() == 'quit':
                    print("👋 Goodbye!")
                    break
                
                if user_input.lower() == 'list':
                    orchestrator.list_agents()
                    continue
                
                # Process the task
                response, used_agent = await orchestrator.send_task(user_input)
                print(f"\n🤖 [{used_agent}]:")
                print(response)
                
            except KeyboardInterrupt:
                print("\n👋 Goodbye!")
                break
            except Exception as e:
                print(f"❌ Error: {e}")


if __name__ == "__main__":
    import sys
    
    if len(sys.argv) > 1 and sys.argv[1] == "--interactive":
        asyncio.run(interactive_mode())
    else:
        asyncio.run(main())
