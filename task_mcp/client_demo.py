"""Demo CLI client: opens the browser for Google sign-in, then calls tools."""
import asyncio

from fastmcp import Client


async def main():
    async with Client("http://localhost:8000/mcp", auth="oauth") as c:
        me = (await c.call_tool("whoami")).data
        print("whoami:", me)
        if me["jira"]["mode"] == "user" and not me["jira"]["connected"]:
            link = (await c.call_tool("connect_jira")).data
            print("\nConnect your Jira account by opening:\n", link["url"])
            input("Press Enter once you have approved access... ")
        print("projects:", (await c.call_tool("list_projects")).data)
        try:
            r = await c.call_tool("create_task", {"project": "KAN", "summary": "SSO test"})
            print("create_task:", r.data)
        except Exception as e:
            print("create_task denied:", e)


asyncio.run(main())
