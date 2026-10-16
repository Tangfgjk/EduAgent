"""仅运行无网络副作用的短图；检查点镜像由调用者事务持久化。"""
import base64
import sqlite3

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command
from langsmith import tracing_context


def invoke(graph, run_id, initial, payload, image):
    with sqlite3.connect(":memory:", check_same_thread=False) as connection:
        if image:
            connection.deserialize(base64.b64decode(image, validate=True))
        compiled = graph.compile(checkpointer=SqliteSaver(connection))
        config = {"configurable": {"thread_id": run_id}, "recursion_limit": 8, "callbacks": []}
        with tracing_context(enabled=False):
            compiled.invoke(Command(resume=payload) if image else initial, config)
            state = compiled.get_state(config)
        connection.commit()
        return state.values, base64.b64encode(connection.serialize()).decode("ascii"), bool(state.next)
