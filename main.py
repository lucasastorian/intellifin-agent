import asyncio
from dotenv import load_dotenv
from agent.agent import Agent

if __name__ == '__main__':
    load_dotenv()

    agent = Agent(edgar_user_agent="Lucas Astorian <lucas@intellifin.ai>")
    result = asyncio.run(agent.run(query="What Apple's revenue as of their latest 10-K?"))
    #
    # print("\n=== Agent completed ===")
    # for msg in agent.messages:
    #     if msg.role == "assistant":
    #         print(f"{msg.content}")
