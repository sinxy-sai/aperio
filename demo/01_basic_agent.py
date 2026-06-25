"""
Demo 01: Basic DeepAgent connectivity test.
Verifies: model connection, basic tool use, FilesystemBackend, write_todos.
"""
import os
from pathlib import Path

from dotenv import load_dotenv
from deepagents import create_deep_agent
from deepagents.backends import FilesystemBackend
from langchain.chat_models import init_chat_model

# Load API key from demo/.env (NOT committed to git)
_demo_dir = Path(__file__).resolve().parent
load_dotenv(_demo_dir / ".env")


def main():
    # 1. Initialize model (uses DEEPSEEK_API_KEY from env, loaded via demo/.env)
    #    DeepSeek uses OpenAI-compatible API, so we use "openai:" prefix
    api_key = os.environ.get("DEEPSEEK_API_KEY", "")
    if not api_key:
        print("ERROR: DEEPSEEK_API_KEY not found in environment or demo/.env")
        print("Create demo/.env with: DEEPSEEK_API_KEY=your-key-here")
        return

    model = init_chat_model(
        model="openai:deepseek-v4-flash",
        api_key=api_key,
        base_url="https://api.deepseek.com",
    )

    # 2. Create backend for file I/O
    backend = FilesystemBackend(
        root_dir="demo/workspace_01",
        virtual_mode=True,
    )

    # 3. Create agent with minimal tools
    agent = create_deep_agent(
        model=model,
        backend=backend,
        system_prompt="""You are a helpful assistant. When asked to research,
use write_todos to plan, write files to organize information, and read files to recall.""",
    )

    # 4. Run a simple task
    print("=" * 60)
    print("Demo 01: Basic Agent Connectivity Test")
    print("=" * 60)

    result = agent.invoke({
        "messages": [
            {
                "role": "user",
                "content": (
                    "请用中文回答：1) 你是谁？2) 简单介绍一下 LangGraph 框架（50字以内）。"
                    "把回答写入 demo/workspace_01/intro.md"
                ),
            }
        ]
    })

    # 5. Print result
    final_msg = result["messages"][-1]
    print(f"\nFinal response:\n{final_msg.content}")
    print("\n✅ Demo 01 complete!")


if __name__ == "__main__":
    main()
