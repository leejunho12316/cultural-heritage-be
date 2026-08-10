"""보고서 생성 LangGraph.

고정된 도자기 처리보고서 양식(8개 섹션)을 노드 하나당 섹션 하나씩 채워서
조립하는 선형 그래프. HITL interrupt가 없어 체크포인터 없이 매 요청마다
동기적으로 실행한다 — 편집은 그래프 밖(ASSESSMENT_REPORT 편집 API)에서 한다.
"""

from langgraph.graph import END, START, StateGraph

from .nodes.assemble import assemble_node
from .nodes.conclusion import conclusion_node
from .nodes.guide_stage import (
    bonding_node,
    cleaning_node,
    disassembly_node,
    reinforcement_node,
    restoration_node,
)
from .nodes.header import header_node
from .nodes.pre_investigation import pre_investigation_node
from .state import State

NODE_CHAIN = [
    "header",
    "pre_investigation",
    "disassembly",
    "cleaning",
    "reinforcement",
    "bonding",
    "restoration",
    "conclusion",
    "assemble",
]

_NODE_FUNCS = {
    "header": header_node,
    "pre_investigation": pre_investigation_node,
    "disassembly": disassembly_node,
    "cleaning": cleaning_node,
    "reinforcement": reinforcement_node,
    "bonding": bonding_node,
    "restoration": restoration_node,
    "conclusion": conclusion_node,
    "assemble": assemble_node,
}


def build_graph():
    builder = StateGraph(State)
    for name in NODE_CHAIN:
        builder.add_node(name, _NODE_FUNCS[name])

    builder.add_edge(START, NODE_CHAIN[0])
    for previous, current in zip(NODE_CHAIN, NODE_CHAIN[1:]):
        builder.add_edge(previous, current)
    builder.add_edge(NODE_CHAIN[-1], END)

    return builder.compile()
