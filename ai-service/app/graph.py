import sqlite3
from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.sqlite import SqliteSaver

from .state import State
from .nodes.disassembly import (
  disassembly_checklist_node,
  disassembly_confirm_checklist_node,
  disassembly_tools_node,
  disassembly_confirm_tools_node,
  disassembly_method_node,
  disassembly_confirm_method_node,
)

# disassembly 단계를 구성하는 물리 노드 이름 (실행 순서대로)
DISASSEMBLY_NODE_CHAIN = [
    "disassembly_checklist",
    "disassembly_confirm_checklist",
    "disassembly_tools",
    "disassembly_confirm_tools",
    "disassembly_method",
    "disassembly_confirm_method",
]

# disassembly 4단계 노드만 연결한 테스트 그래프
_disassembly_node_funcs = {
    "disassembly_checklist": disassembly_checklist_node,
    "disassembly_confirm_checklist": disassembly_confirm_checklist_node,
    "disassembly_tools": disassembly_tools_node,
    "disassembly_confirm_tools": disassembly_confirm_tools_node,
    "disassembly_method": disassembly_method_node,
    "disassembly_confirm_method": disassembly_confirm_method_node,
}

def build_graph():
    builder = StateGraph(State)
    for _name in DISASSEMBLY_NODE_CHAIN:
        builder.add_node(_name, _disassembly_node_funcs[_name])
    builder.add_edge(START, DISASSEMBLY_NODE_CHAIN[0])
    for _prev, _nxt in zip(DISASSEMBLY_NODE_CHAIN, DISASSEMBLY_NODE_CHAIN[1:]):
        builder.add_edge(_prev, _nxt)
    builder.add_edge(DISASSEMBLY_NODE_CHAIN[-1], END)

    conn = sqlite3.connect("checkpoints.db", check_same_thread=False)
    graph = builder.compile(checkpointer=SqliteSaver(conn))

    return graph